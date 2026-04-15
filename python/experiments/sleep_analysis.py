#!/usr/bin/env python3
"""
sleep_analysis.py — Offline sleep stage scoring and event detection
===================================================================
Processes a full-night EEG recording from the wearable EDF and produces:

  1. Hypnogram            (30 s AASM epochs: Wake / N1 / N2 / N3 / REM)
  2. Overnight spectrogram panel (delta / sigma / beta power + EOG + EMG)
  3. Sleep spindle detection  (12–15 Hz sigma bursts, 0.5–3 s)
  4. Slow-wave detection      (0.5–2 Hz, peak-to-peak > 75 µV)
  5. REM episode detection    (EOG bursts + EMG atonia coincidence)
  6. Motion artifact flags    (from IMU accelerometer)
  7. Summary statistics figure

Scoring is rule-based (no ML) using relative EEG band powers, EMG tone,
and EOG activity — an approximation of AASM manual scoring. Expect ~70–80 %
agreement with gold-standard PSG on a wearable system.

Usage:
    python sleep_analysis.py --edf ../recordings/eeg_YYYYMMDD_HHMMSS.edf
    python sleep_analysis.py --edf <path>  --out figures/

Channels used:
    EEG_L2  (central-ish) — primary: spindles, K-complexes, band power
    EEG_L1  (occipital)   — slow waves, delta
    EEG_L3  (frontal)     — frontal slow waves
    EOG                   — eye movements, REM burst detection
    EMG_far               — muscle tone (HP >50 Hz), REM atonia detection
    ACCEL_X/Y/Z           — motion artifact flagging (25 Hz)
"""

from __future__ import annotations

import os
import sys
import argparse
import warnings
import numpy as np
import matplotlib
matplotlib.use("Agg")   # no GUI — 6-hour recordings are too slow for interactive
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy.signal import butter, sosfilt, hilbert, welch, medfilt
from scipy.ndimage import uniform_filter1d

warnings.filterwarnings("ignore", category=RuntimeWarning)


# ── Global constants ──────────────────────────────────────────────────────────

FS_EEG  = 250    # EEG sample rate Hz
FS_IMU  = 25     # IMU sample rate Hz
EPOCH_S = 30     # AASM standard epoch length
EPOCH_N = FS_EEG * EPOCH_S

# EDF channel labels (must match what eeg_stream_pg.py writes)
CH_EEG_CENTRAL  = "EEG_L2"
CH_EEG_OCCIP    = "EEG_L1"
CH_EEG_FRONTAL  = "EEG_L3"
CH_EOG          = "EOG"
CH_EMG          = "EMG_far"
CH_ACCEL        = ["ACCEL_X", "ACCEL_Y", "ACCEL_Z"]

# Sleep stage codes
WAKE, N1, N2, N3, REM = 0, 1, 2, 3, 4
STAGE_LABELS  = {WAKE: "Wake", N1: "N1",  N2: "N2",  N3: "N3",  REM: "REM"}
STAGE_Y       = {WAKE: 4,      N1: 3,     N2: 2,     N3: 1,     REM: 3.5}
STAGE_COLORS  = {
    WAKE: "#e53935", N1: "#fb8c00", N2: "#43a047", N3: "#1e88e5", REM: "#8e24aa"
}

# Band definitions (Hz)
BANDS = {
    "delta": (0.5,  4.0),
    "theta": (4.0,  8.0),
    "alpha": (8.0, 13.0),
    "sigma": (12.0, 15.0),
    "beta":  (15.0, 30.0),
}


# ── EDF loading ────────────────────────────────────────────────────────────────

def load_edf(path: str) -> dict[str, np.ndarray]:
    """Load EDF, return {label: 1-D float64 array}. Mixed sample rates OK."""
    import pyedflib
    f = pyedflib.EdfReader(path)
    out = {}
    for i in range(f.signals_in_file):
        lbl = f.getLabel(i).strip()
        out[lbl] = np.array(f.readSignal(i), dtype=np.float64)
    f.close()
    print(f"[LOAD] {os.path.basename(path)}")
    for lbl, arr in out.items():
        print(f"       {lbl:15s}  {len(arr):>8d} samples  "
              f"({len(arr)/FS_EEG:.0f} s at {FS_EEG} Hz)" if "EEG" in lbl or lbl in
              (CH_EOG, CH_EMG) else
              f"       {lbl:15s}  {len(arr):>8d} samples")
    return out


# ── Fitbit ground truth parsing ───────────────────────────────────────────────

# Mapping from Fitbit 4-stage labels to our 5-stage codes.
# Fitbit "Light" covers N1+N2; we map to N2 (most of light sleep is N2).
FITBIT_MAP = {"Awake": WAKE, "Light": N2, "Deep": N3, "REM": REM}

def parse_fitbit(gt: dict, rec_start_epoch: float, n_epochs: int,
                 epoch_s: float = 30.0) -> np.ndarray | None:
    """
    Convert FITBIT_GROUND_TRUTH segments to a stage array aligned to the EDF.

    Each segment is a (start_HH:MM, end_HH:MM, stage) tuple.  Times are
    resolved against gt["date"] (the date sleep *started*) and midnight
    crossings are detected by checking if a start time precedes the previous
    segment's end time — if so, one day is added.

    Returns an int array of shape (n_epochs,) with stage codes, or None if
    the segments list is empty.
    """
    from datetime import datetime, timedelta

    segs = gt.get("segments", [])
    if not segs:
        return None

    base_date = datetime.strptime(gt["date"], "%Y-%m-%d")

    # Parse all start/end times, rolling over midnight when needed
    parsed = []
    day_offset = timedelta(0)
    prev_end_dt = None

    for start_str, end_str, stage in segs:
        sh, sm = start_str.split(":")
        eh, em = end_str.split(":")
        start_dt = base_date + day_offset + timedelta(hours=int(sh), minutes=int(sm))
        end_dt   = base_date + day_offset + timedelta(hours=int(eh), minutes=int(em))

        # If start is before previous segment's end, we crossed midnight
        if prev_end_dt is not None and start_dt < prev_end_dt:
            day_offset += timedelta(days=1)
            start_dt += timedelta(days=1)
            end_dt   += timedelta(days=1)
        # If end < start (e.g. 23:50 → 00:05), end also crossed midnight
        if end_dt < start_dt:
            end_dt += timedelta(days=1)

        prev_end_dt = end_dt
        parsed.append((start_dt.timestamp(), end_dt.timestamp(),
                        FITBIT_MAP.get(stage, N1)))

    # Fitbit labels sessions with the date sleep *started*, which for late-night
    # sessions is the previous calendar day.  If all parsed timestamps land more
    # than 20 hours before rec_start_epoch, shift everything forward by one day.
    if parsed and (rec_start_epoch - parsed[0][0]) > 20 * 3600:
        parsed = [(t0 + 86400, t1 + 86400, code) for t0, t1, code in parsed]
        print("[FITBIT] Applied +1 day offset (Fitbit prev-day date convention)")

    # Fill epoch array — default to Wake for gaps / out-of-range
    out = np.full(n_epochs, WAKE, dtype=int)
    for t0, t1, code in parsed:
        e0 = max(0, int((t0 - rec_start_epoch) / epoch_s))
        e1 = min(n_epochs, int((t1 - rec_start_epoch) / epoch_s))
        if e1 > e0:
            out[e0:e1] = code

    return out


def agreement_stats(predicted: np.ndarray,
                    reference: np.ndarray) -> dict:
    """
    Compute accuracy and Cohen's kappa between predicted and Fitbit stages.

    Because Fitbit has no N1/N2 distinction, comparison is done on a merged
    4-stage scheme: Wake, Light (N1+N2), Deep (N3), REM.
    """
    def _merge(stages):
        # N1 (1) and N2 (2) → 1 (Light);  N3 (3) → 2 (Deep);  REM (4) → 3;  Wake → 0
        m = stages.copy()
        m[m == N2] = N1      # N2 → Light bucket
        m[m == N3] = 2       # N3 → Deep
        m[m == REM] = 3
        return m

    p = _merge(predicted)
    r = _merge(reference)
    n = min(len(p), len(r))
    p, r = p[:n], r[:n]

    acc = float(np.mean(p == r))

    # Cohen's kappa
    classes = [0, 1, 2, 3]
    n_cls   = len(classes)
    conf    = np.zeros((n_cls, n_cls), dtype=int)
    for i, ci in enumerate(classes):
        for j, cj in enumerate(classes):
            conf[i, j] = int(np.sum((r == ci) & (p == cj)))
    po  = acc
    pe  = sum((conf[i, :].sum() / n) * (conf[:, i].sum() / n) for i in range(n_cls))
    kappa = (po - pe) / (1 - pe) if (1 - pe) > 1e-9 else 0.0

    # Per-stage stats
    stage_names = {0: "Wake", 1: "Light", 2: "Deep", 3: "REM"}
    per_stage   = {}
    for i, name in stage_names.items():
        tp = conf[i, i]
        fn = conf[i, :].sum() - tp
        fp = conf[:, i].sum() - tp
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        per_stage[name] = {"recall": recall, "precision": precision}

    return {"accuracy": acc, "kappa": kappa, "per_stage": per_stage,
            "confusion": conf}


def plot_comparison(predicted: np.ndarray, fitbit: np.ndarray,
                    epoch_s: float = 30.0,
                    stats: dict | None = None) -> plt.Figure:
    """
    Stacked hypnogram comparison: EEG (predicted) on top, Fitbit below.
    Agreement confusion matrix on the right.
    """
    n  = min(len(predicted), len(fitbit))
    t  = np.arange(n) * epoch_s / 3600.0

    fig, (ax_eeg, ax_fit) = plt.subplots(2, 1, figsize=(20, 6),
                                          sharex=True, gridspec_kw={"hspace": 0.35})

    def _merge4(s):
        m = s.copy().astype(int)
        m[m == N2] = N1
        m[m == N3] = 2
        m[m == REM] = 3
        return m

    def _draw_hyp(ax, stages, title):
        for e in range(n):
            st = int(stages[e])
            ax.barh(STAGE_Y[st], epoch_s / 3600.0, left=t[e],
                    height=0.75, color=STAGE_COLORS[st], alpha=0.8, linewidth=0)
        ax.set_yticks([STAGE_Y[s] for s in [WAKE, REM, N1, N2, N3]])
        ax.set_yticklabels(["Wake", "REM", "N1", "N2", "N3"])
        ax.set_xlim(t[0], t[-1] + epoch_s / 3600.0)
        ax.set_ylim(0.4, 4.8)
        ax.set_title(title, fontweight="bold")

    _draw_hyp(ax_eeg, predicted[:n], "EEG wearable (predicted)")
    _draw_hyp(ax_fit, fitbit[:n],    "Fitbit ground truth")
    ax_fit.set_xlabel("Time (hours)")

    if stats:
        ax_eeg.text(0.01, 0.97,
                    f"Accuracy {stats['accuracy']*100:.0f}%  k={stats['kappa']:.2f}",
                    transform=ax_eeg.transAxes, fontsize=8,
                    va="top", color="#333",
                    bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#aaa", lw=0.8))

    fig.suptitle("EEG Wearable vs Fitbit — Sleep Stage Agreement",
                 fontsize=11, fontweight="bold")
    return fig


def plot_hypnogram_overlay(predicted: np.ndarray, fitbit: np.ndarray,
                           epoch_s: float = 30.0) -> plt.Figure:
    """Both hypnograms on the same axes — EEG solid, Fitbit dashed."""
    n = min(len(predicted), len(fitbit))
    t = np.arange(n) * epoch_s / 3600.0

    fig, ax = plt.subplots(figsize=(14, 3.5))
    fig.suptitle("Sleep Hypnogram — EEG Wearable vs Fitbit",
                 fontweight="bold", y=1.01)

    def _step(stages):
        y = [STAGE_Y[int(s)] for s in stages]
        step_t = np.repeat(t, 2)[1:]
        step_t = np.append(step_t, t[-1] + epoch_s / 3600.0)
        step_y = np.repeat(y, 2)
        return np.concatenate([[t[0]], step_t]), np.concatenate([[y[0]], step_y])

    tx, yx = _step(predicted[:n])
    tf, yf = _step(fitbit[:n])

    ax.plot(tx, yx, color="#1e88e5", linewidth=1.4, label="EEG wearable (predicted)")
    ax.plot(tf, yf, color="#e53935", linewidth=1.4, linestyle="--",
            alpha=0.8, label="Fitbit ground truth")

    ax.set_yticks([STAGE_Y[s] for s in [WAKE, REM, N1, N2, N3]])
    ax.set_yticklabels(["Wake", "REM", "N1", "N2", "N3"])
    ax.set_xlabel("Time (hours)")
    ax.set_xlim(t[0], t[-1] + epoch_s / 3600.0)
    ax.set_ylim(0.4, 4.8)
    ax.legend(loc="upper right", fontsize=9, framealpha=0.8)
    fig.tight_layout()
    return fig


# ── Filter helpers ─────────────────────────────────────────────────────────────

def _bp(lo: float, hi: float, fs: int = FS_EEG, order: int = 4) -> np.ndarray:
    nyq = fs / 2.0
    return butter(order, [max(lo, 0.1) / nyq, min(hi, nyq - 0.5) / nyq],
                  btype="band", output="sos")

def _hp(cutoff: float, fs: int = FS_EEG, order: int = 2) -> np.ndarray:
    return butter(order, cutoff / (fs / 2.0), btype="high", output="sos")

def _lp(cutoff: float, fs: int = FS_EEG, order: int = 4) -> np.ndarray:
    return butter(order, cutoff / (fs / 2.0), btype="low", output="sos")

def filt(sig: np.ndarray, sos: np.ndarray) -> np.ndarray:
    return sosfilt(sos, sig)


# ── Band-power per epoch ───────────────────────────────────────────────────────

def _epoch_bandpowers(sig: np.ndarray, fs: int = FS_EEG) -> dict[str, np.ndarray]:
    """
    For each 30 s epoch compute band power for delta/theta/alpha/sigma/beta.

    Normalization is band-specific to prevent 1/f noise from collapsing
    high-frequency bands to zero in relative terms:
      delta  — relative to total 0.5–40 Hz  (keeps N3 threshold meaningful)
      others — relative to 4–40 Hz only     (removes delta-dominance so
                                              sigma/alpha variation is visible)

    Returns dict of {band: array shape (n_epochs,)}.
    """
    n_epochs = len(sig) // EPOCH_N
    nperseg  = min(EPOCH_N, fs * 4)

    # Pre-build band-pass filters for each band
    sos_bands  = {name: _bp(lo, hi, fs) for name, (lo, hi) in BANDS.items()}
    total_sos  = _bp(0.5, 40.0, fs)   # denominator for delta
    hf_sos     = _bp(4.0,  40.0, fs)  # denominator for theta/alpha/sigma/beta

    result = {name: np.zeros(n_epochs) for name in BANDS}

    for e in range(n_epochs):
        seg = sig[e * EPOCH_N : (e + 1) * EPOCH_N]

        f_t, p_t = welch(filt(seg, total_sos), fs=fs, nperseg=nperseg)
        total_p  = np.trapz(p_t, f_t) + 1e-30

        f_h, p_h = welch(filt(seg, hf_sos), fs=fs, nperseg=nperseg)
        hf_p     = np.trapz(p_h, f_h) + 1e-30

        for name, (lo, hi) in BANDS.items():
            f_b, p_b = welch(filt(seg, sos_bands[name]), fs=fs, nperseg=nperseg)
            mask = (f_b >= lo) & (f_b <= hi)
            band_p = np.trapz(p_b[mask], f_b[mask])
            # delta uses full-spectrum denominator; all other bands use 4–40 Hz
            denom = total_p if name == "delta" else hf_p
            result[name][e] = band_p / denom

    return result


def _epoch_emg_rms(sig: np.ndarray, fs: int = FS_EEG) -> np.ndarray:
    """RMS of HP-filtered (>50 Hz) EMG per 30 s epoch."""
    sos = _hp(50.0, fs)
    hp  = filt(sig, sos)
    n_epochs = len(sig) // EPOCH_N
    rms = np.zeros(n_epochs)
    for e in range(n_epochs):
        seg = hp[e * EPOCH_N : (e + 1) * EPOCH_N]
        rms[e] = np.sqrt(np.mean(seg ** 2))
    return rms


def _epoch_eog_activity(sig: np.ndarray, fs: int = FS_EEG) -> np.ndarray:
    """
    EOG activity per epoch: RMS of 0.1–5 Hz bandpass.
    High in Wake (blinks/saccades) and REM (rapid eye movements).
    """
    sos = _bp(0.1, 5.0, fs)
    bp  = filt(sig, sos)
    n_epochs = len(sig) // EPOCH_N
    act = np.zeros(n_epochs)
    for e in range(n_epochs):
        seg = bp[e * EPOCH_N : (e + 1) * EPOCH_N]
        act[e] = np.sqrt(np.mean(seg ** 2))
    return act


def _epoch_motion(accel_arrays: list[np.ndarray]) -> np.ndarray:
    """
    Motion energy per 30 s epoch from IMU accelerometer.
    Uses 30 s epochs at IMU_FS. Returns (n_epochs,) array.
    """
    epoch_imu = FS_IMU * EPOCH_S
    mag = np.sqrt(sum(a ** 2 for a in accel_arrays))
    # High-pass to remove gravity (DC)
    sos = _hp(0.1, FS_IMU, order=2)
    motion = np.abs(filt(mag, sos))
    n_epochs = len(motion) // epoch_imu
    out = np.zeros(n_epochs)
    for e in range(n_epochs):
        seg = motion[e * epoch_imu : (e + 1) * epoch_imu]
        out[e] = np.mean(seg)
    return out


# ── Sleep stage scoring ────────────────────────────────────────────────────────

def score_hypnogram(bands: dict[str, np.ndarray],
                    emg_rms: np.ndarray,
                    eog_act: np.ndarray,
                    motion:  np.ndarray | None = None) -> np.ndarray:
    """
    Rule-based AASM-simplified sleep stage classification.

    Features (all relative / z-scored for robustness):
      delta_rel  — slow-wave dominance → N3
      sigma_rel  — spindle-band power  → N2
      alpha_rel  — alpha              → Wake/N1
      emg_z      — muscle tone z-score → high=Wake/artifact, low=REM/sleep
      eog_z      — eye movement z-score → high=Wake/REM, low=sleep

    Rules (applied in priority order):
      1. Motion artifact                                         → Wake
      2. emg_z > 1.5                                            → Wake
      3. delta > 80th pct                                       → N3
      4. delta > 65th pct                                       → N3
      5. sigma < 40th pct AND delta < 45th pct AND emg_z < 1.0 → REM
      6. sigma > 60th pct AND emg_z < 1.0                      → N2
      7. alpha > 80th pct                                       → Wake (after N2)
      8. beta  > 85th pct                                       → Wake (residual)
      9. Everything else                                        → N1

    Returns array of stage codes shape (n_epochs,).
    """
    n = len(emg_rms)
    stages = np.full(n, N1, dtype=int)

    delta  = bands["delta"]
    sigma  = bands["sigma"]
    alpha  = bands["alpha"]
    beta   = bands["beta"]

    # Adaptive thresholds — percentile-based so they work on any recording
    def _z(x):
        std = x.std()
        return (x - x.mean()) / (std if std > 1e-12 else 1.0)

    emg_z   = _z(emg_rms)
    eog_z   = _z(eog_act)
    delta_z = _z(delta)

    sigma_thresh     = np.percentile(sigma, 60)  # top 40% sigma → spindle activity → N2
    alpha_thresh     = np.percentile(alpha, 70)  # top 30% alpha → wakefulness / N1
    sigma_rem_thresh = np.percentile(sigma, 40)  # sigma low in REM (spindles absent)

    # Adaptive delta thresholds — within-recording percentiles.
    # Absolute thresholds (0.30/0.40) break on wearable/dry-electrode recordings
    # where the 1/f noise floor elevates delta relative power across ALL epochs.
    delta_thresh_soft = np.percentile(delta, 80)  # top 20% → N3
    delta_rem_thresh  = np.percentile(delta, 45)  # below 45th pct → no slow waves (REM/N2/W)

    print(f"  [DBG] delta percentiles  45={delta_rem_thresh:.3f}  "
          f"80={delta_thresh_soft:.3f}")
    print(f"  [DBG] sigma percentiles  40={sigma_rem_thresh:.3f}  "
          f"60={sigma_thresh:.3f}  |  alpha 70th={alpha_thresh:.3f}")

    alpha_wake_thresh = np.percentile(alpha, 80)  # tighter than staging thresh
    beta_wake_thresh  = np.percentile(beta,  85)  # beta unreliable (EMG bleed); high bar
    motion_thresh     = np.percentile(motion, 95) if motion is not None else None

    for e in range(n):
        # ── 1. Motion artifact → Wake ──────────────────────────────────────
        if motion_thresh is not None and e < len(motion):
            if motion[e] > motion_thresh:
                stages[e] = WAKE
                continue

        # ── 2. High EMG → Wake (before N3 so wakefulness epochs with
        #     coincidentally high delta aren't buried in slow-wave sleep)
        if emg_z[e] > 1.5:
            stages[e] = WAKE
            continue

        # ── 3. N3: elevated delta (top 20% within recording)
        if delta[e] > delta_thresh_soft:
            stages[e] = N3
            continue

        # ── 4. REM: low sigma (spindles absent) + low delta + not high EMG
        #     EMG threshold 1.0 — EMG_far is not submental, atonia is weak.
        if (sigma[e] < sigma_rem_thresh
                and delta[e] < delta_rem_thresh
                and emg_z[e] < 1.0):
            stages[e] = REM
            continue

        # ── 6. N2: spindle band elevated ───────────────────────────────────
        if sigma[e] > sigma_thresh and emg_z[e] < 1.0:
            stages[e] = N2
            continue

        # ── 7. High alpha → Wake (after N2 so N2 epochs with alpha intrusions
        #     aren't misclassified; 80th pct threshold = top 20% only)
        if alpha[e] > alpha_wake_thresh:
            stages[e] = WAKE
            continue

        # ── 8. High beta → Wake (residual; 85th pct — high bar since dry
        #     electrodes bleed EMG into beta band)
        if beta[e] > beta_wake_thresh:
            stages[e] = WAKE
            continue

        # ── 9. Default: N1 (transitional) ──────────────────────────────────
        stages[e] = N1

    # Smooth: remove isolated single-epoch stage changes (median filter width 3)
    stages = np.array([int(round(x)) for x in medfilt(stages.astype(float), 3)])
    return stages


# ── Sleep spindle detection ───────────────────────────────────────────────────

def detect_spindles(sig: np.ndarray, fs: int = FS_EEG,
                    lo: float = 12.0, hi: float = 15.0,
                    min_dur_s: float = 0.5, max_dur_s: float = 3.0,
                    threshold_factor: float = 2.5
                    ) -> list[dict]:
    """
    Detect sleep spindles in the sigma band.

    Method:
      1. Bandpass 12–15 Hz
      2. Envelope via Hilbert transform
      3. Baseline = rolling 30 s RMS
      4. Threshold = baseline × threshold_factor
      5. Keep events whose duration is in [min_dur_s, max_dur_s]

    Returns list of dicts: {start_s, end_s, duration_s, peak_amp_uv, peak_freq_hz}
    """
    sos      = _bp(lo, hi, fs)
    filtered = filt(sig, sos)
    envelope = np.abs(hilbert(filtered))

    # Rolling baseline (30 s window)
    win = int(fs * 30)
    baseline = uniform_filter1d(envelope, size=win)
    threshold = baseline * threshold_factor

    above = envelope > threshold
    # Find contiguous regions above threshold
    changes  = np.diff(above.astype(int))
    starts   = np.where(changes == 1)[0] + 1
    ends     = np.where(changes == -1)[0] + 1

    # Handle edge cases
    if above[0]:
        starts = np.concatenate([[0], starts])
    if above[-1]:
        ends = np.concatenate([ends, [len(above)]])

    spindles = []
    min_samp = int(min_dur_s * fs)
    max_samp = int(max_dur_s * fs)

    # Skip first 30 s — baseline is near-zero so threshold is meaningless
    skip_samp = int(fs * 30)

    for s, e in zip(starts, ends):
        dur = e - s
        if s < skip_samp:
            continue
        if min_samp <= dur <= max_samp:
            peak_amp = float(np.max(envelope[s:e]))
            if peak_amp < 20.0:   # absolute floor — below this is noise, not a spindle
                continue
            seg = filtered[s:e]
            # Instantaneous frequency via zero-crossings
            zc = np.where(np.diff(np.sign(seg)))[0]
            if len(zc) >= 2:
                peak_freq = (len(zc) / 2) / (dur / fs)
            else:
                peak_freq = (lo + hi) / 2
            spindles.append({
                "start_s":    s / fs,
                "end_s":      e / fs,
                "duration_s": dur / fs,
                "peak_amp_uv": peak_amp,
                "peak_freq_hz": float(peak_freq),
            })

    return spindles


# ── Slow-wave detection ───────────────────────────────────────────────────────

def detect_slow_waves(sig: np.ndarray, fs: int = FS_EEG,
                      min_pp_uv: float = 75.0,
                      min_dur_s: float = 0.25, max_dur_s: float = 1.0
                      ) -> list[dict]:
    """
    Detect slow waves (delta/SO) via bandpass + zero-crossing method.

    Method (Mölle et al.):
      1. Bandpass 0.5–2 Hz
      2. Find negative-to-positive zero crossings (SO negative half-wave start)
      3. Measure peak-to-peak amplitude between consecutive crossings
      4. Keep events with amplitude > min_pp_uv and half-wave in [min_dur, max_dur]

    Returns list of dicts: {start_s, end_s, neg_peak_uv, pos_peak_uv, pp_uv}
    """
    sos      = _bp(0.5, 2.0, fs)
    filtered = filt(sig, sos)

    # Zero crossings
    signs    = np.sign(filtered)
    # neg→pos crossings (start of positive half-wave, end of negative half-wave)
    neg2pos  = np.where((signs[:-1] <= 0) & (signs[1:] > 0))[0]

    slow_waves = []
    for i in range(len(neg2pos) - 1):
        s = neg2pos[i]
        e = neg2pos[i + 1]
        dur = (e - s) / fs
        if not (min_dur_s <= dur <= max_dur_s):
            continue
        seg     = filtered[s:e]
        neg_pk  = float(np.min(seg))
        pos_pk  = float(np.max(seg))
        pp      = pos_pk - neg_pk
        if pp >= min_pp_uv:
            slow_waves.append({
                "start_s":   s / fs,
                "end_s":     e / fs,
                "neg_peak_uv": neg_pk,
                "pos_peak_uv": pos_pk,
                "pp_uv":     pp,
            })

    return slow_waves


# ── REM rapid eye movement detection ─────────────────────────────────────────

def detect_rem_bursts(eog: np.ndarray, emg: np.ndarray,
                      stages: np.ndarray, fs: int = FS_EEG,
                      min_amp_uv: float = 25.0) -> list[dict]:
    """
    Detect rapid eye movements within REM epochs.

    Method:
      1. Restrict to REM-scored epochs
      2. Bandpass EOG 0.5–5 Hz
      3. Find peaks with amplitude > min_amp_uv and inter-peak interval < 2 s
      4. Require coincident EMG to be at or below REM-baseline level

    Returns list of dicts: {time_s, amplitude_uv}
    """
    from scipy.signal import find_peaks

    sos_eog  = _bp(0.5, 5.0, fs)
    sos_emg  = _hp(50.0, fs)
    eog_filt = filt(eog, sos_eog)
    emg_hp   = filt(emg, sos_emg)

    # Build a mask for samples that fall in REM epochs
    rem_mask = np.zeros(len(eog), dtype=bool)
    for e, st in enumerate(stages):
        if st == REM:
            s0 = e * EPOCH_N
            s1 = min(len(eog), (e + 1) * EPOCH_N)
            rem_mask[s0:s1] = True

    if not rem_mask.any():
        return []

    # Adaptive threshold from the REM segments
    rem_eog    = np.abs(eog_filt[rem_mask])
    rem_thresh = max(min_amp_uv, np.percentile(rem_eog, 85))

    # EMG atonia threshold
    emg_rms_rem = np.sqrt(np.mean(emg_hp[rem_mask] ** 2))

    peaks, props = find_peaks(np.abs(eog_filt),
                              height=rem_thresh,
                              distance=int(0.3 * fs))   # at least 300 ms apart

    bursts = []
    for pk in peaks:
        if not rem_mask[pk]:
            continue
        # Check local EMG is low (atonia)
        s0 = max(0, pk - EPOCH_N // 2)
        s1 = min(len(emg_hp), pk + EPOCH_N // 2)
        local_emg = np.sqrt(np.mean(emg_hp[s0:s1] ** 2))
        if local_emg < emg_rms_rem * 2.0:
            bursts.append({
                "time_s":      pk / fs,
                "amplitude_uv": float(np.abs(eog_filt[pk])),
            })

    return bursts


# ── Rolling spectrogram (time × frequency) for overnight plot ─────────────────

def rolling_bandpower_timeseries(sig: np.ndarray, fs: int = FS_EEG,
                                  epoch_s: float = 30.0
                                  ) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """
    Compute per-epoch relative band power for the full recording.
    Returns (time_hours, {band: power_array}).
    """
    epoch_n  = int(fs * epoch_s)
    n_epochs = len(sig) // epoch_n
    t_hours  = np.arange(n_epochs) * epoch_s / 3600.0

    out = {name: np.zeros(n_epochs) for name in BANDS}
    sos_total = _bp(0.5, 40.0, fs)
    sos_bands = {name: _bp(lo, hi, fs) for name, (lo, hi) in BANDS.items()}
    nperseg   = min(epoch_n, fs * 4)

    for e in range(n_epochs):
        seg   = sig[e * epoch_n : (e + 1) * epoch_n]
        _, pt = welch(filt(seg, sos_total), fs=fs, nperseg=nperseg)
        total = pt.sum() + 1e-30
        for name, (lo, hi) in BANDS.items():
            f_b, pb = welch(filt(seg, sos_bands[name]), fs=fs, nperseg=nperseg)
            mask = (f_b >= lo) & (f_b <= hi)
            out[name][e] = pb[mask].sum() / total

    return t_hours, out


# ── Plotting ──────────────────────────────────────────────────────────────────

plt.rcParams.update({
    "font.family": "serif", "font.size": 9,
    "axes.titlesize": 10, "axes.labelsize": 9,
    "xtick.labelsize": 8, "ytick.labelsize": 8,
    "figure.facecolor": "white", "axes.spines.top": False,
    "axes.spines.right": False,
})


def _hours(samples: int, fs: int = FS_EEG) -> float:
    return samples / fs / 3600.0


def plot_hypnogram(stages: np.ndarray, epoch_s: float = 30.0,
                   spindles: list | None = None,
                   slow_waves: list | None = None,
                   rem_bursts: list | None = None,
                   motion: np.ndarray | None = None) -> plt.Figure:
    """Main hypnogram figure."""
    n    = len(stages)
    t    = np.arange(n) * epoch_s / 3600.0   # hours

    fig, ax = plt.subplots(figsize=(14, 3.5))
    fig.suptitle("Sleep Hypnogram", fontweight="bold", y=1.01)

    # Shaded stage blocks
    for e in range(n):
        st  = stages[e]
        col = STAGE_COLORS[st]
        ax.barh(STAGE_Y[st], epoch_s / 3600.0, left=t[e],
                height=0.75, color=col, alpha=0.75, linewidth=0)

    # Step-plot outline
    step_t = np.repeat(t, 2)[1:]
    step_t = np.append(step_t, t[-1] + epoch_s / 3600.0)
    step_y = np.repeat([STAGE_Y[s] for s in stages], 2)
    ax.step(np.concatenate([[t[0]], step_t]),
            np.concatenate([[STAGE_Y[stages[0]]], step_y]),
            color="#222", linewidth=0.9, where="post")

    # Spindle markers (tick marks at top of N2 bar)
    if spindles:
        sp_t = [s["start_s"] / 3600.0 for s in spindles]
        ax.vlines(sp_t, 1.65, 2.35, color="#43a047", linewidth=0.5, alpha=0.6,
                  label=f"Spindles (n={len(spindles)})")

    # Slow-wave markers
    if slow_waves:
        sw_t = [s["start_s"] / 3600.0 for s in slow_waves]
        ax.vlines(sw_t, 0.65, 1.35, color="#1e88e5", linewidth=0.5, alpha=0.5,
                  label=f"Slow waves (n={len(slow_waves)})")

    # REM burst markers
    if rem_bursts:
        rb_t = [r["time_s"] / 3600.0 for r in rem_bursts]
        ax.vlines(rb_t, 3.15, 3.85, color="#8e24aa", linewidth=0.6, alpha=0.6,
                  label=f"REM bursts (n={len(rem_bursts)})")

    # Motion artifact overlay
    if motion is not None:
        motion_thresh = np.percentile(motion, 90)
        for e, m in enumerate(motion):
            if m > motion_thresh and e < n:
                ax.axvspan(t[e], t[e] + epoch_s / 3600.0,
                           ymin=0, ymax=1, color="#ff7043", alpha=0.15)

    ax.set_yticks([STAGE_Y[s] for s in [WAKE, REM, N1, N2, N3]])
    ax.set_yticklabels(["Wake", "REM", "N1", "N2", "N3"])
    ax.set_xlabel("Time (hours)")
    ax.set_xlim(t[0], t[-1] + epoch_s / 3600.0)
    ax.set_ylim(0.4, 4.8)
    if spindles or slow_waves or rem_bursts:
        ax.legend(loc="upper right", fontsize=7, framealpha=0.7)
    fig.tight_layout()
    return fig


def plot_overnight_panel(stages: np.ndarray,
                         t_hours: np.ndarray,
                         band_power: dict[str, np.ndarray],
                         eog: np.ndarray,
                         emg: np.ndarray,
                         epoch_s: float = 30.0) -> plt.Figure:
    """
    4-panel overnight overview:
      Top:    Hypnogram
      Panel 2: Delta + Sigma band power over time
      Panel 3: EOG RMS (eye movement activity)
      Panel 4: EMG RMS (muscle tone)
    """
    fig = plt.figure(figsize=(15, 9))
    gs  = gridspec.GridSpec(4, 1, figure=fig, hspace=0.45,
                            height_ratios=[1.2, 1, 1, 1])
    ax0 = fig.add_subplot(gs[0])   # hypnogram
    ax1 = fig.add_subplot(gs[1])   # band power
    ax2 = fig.add_subplot(gs[2])   # EOG
    ax3 = fig.add_subplot(gs[3])   # EMG

    n = len(stages)
    t_ep = np.arange(n) * epoch_s / 3600.0

    # ── Hypnogram ─────────────────────────────────────────────────────────────
    for e in range(n):
        st = stages[e]
        ax0.barh(STAGE_Y[st], epoch_s / 3600.0, left=t_ep[e],
                 height=0.75, color=STAGE_COLORS[st], alpha=0.8, linewidth=0)
    ax0.set_yticks([STAGE_Y[s] for s in [WAKE, REM, N1, N2, N3]])
    ax0.set_yticklabels(["Wake", "REM", "N1", "N2", "N3"], fontsize=8)
    ax0.set_xlim(t_ep[0], t_ep[-1] + epoch_s / 3600.0)
    ax0.set_ylim(0.4, 4.8)
    ax0.set_title("Hypnogram", fontweight="bold")
    ax0.set_xticklabels([])

    # ── Band power ────────────────────────────────────────────────────────────
    ax1.fill_between(t_hours, band_power["delta"], alpha=0.55,
                     color="#1e88e5", label="Delta (0.5–4 Hz)")
    ax1.fill_between(t_hours, band_power["sigma"], alpha=0.65,
                     color="#43a047", label="Sigma (12–15 Hz, spindles)")
    ax1.fill_between(t_hours, band_power["beta"],  alpha=0.4,
                     color="#e53935", label="Beta (15–30 Hz)")
    ax1.set_ylabel("Relative power")
    ax1.set_title("EEG band power (EEG_L2 central)", fontweight="bold")
    ax1.legend(loc="upper right", fontsize=7, framealpha=0.7)
    ax1.set_xlim(t_ep[0], t_ep[-1])
    ax1.set_xticklabels([])

    # ── EOG activity ──────────────────────────────────────────────────────────
    eog_rms = _epoch_eog_activity(eog)[:n]
    t_eog   = np.arange(len(eog_rms)) * epoch_s / 3600.0
    # Shade REM epochs behind EOG trace
    for e in range(min(n, len(eog_rms))):
        if stages[e] == REM:
            ax2.axvspan(t_eog[e], t_eog[e] + epoch_s / 3600.0,
                        color="#8e24aa", alpha=0.12)
    ax2.plot(t_eog[:n], eog_rms[:n], color="#4fc3f7", linewidth=0.7)
    ax2.set_ylabel("EOG RMS (µV)")
    ax2.set_title("EOG activity  (purple = REM epochs)", fontweight="bold")
    ax2.set_xlim(t_ep[0], t_ep[-1])
    ax2.set_xticklabels([])

    # ── EMG tone ──────────────────────────────────────────────────────────────
    emg_rms = _epoch_emg_rms(emg)[:n]
    t_emg   = np.arange(len(emg_rms)) * epoch_s / 3600.0
    # Shade REM epochs (should show atonia — EMG drops)
    for e in range(min(n, len(emg_rms))):
        if stages[e] == REM:
            ax3.axvspan(t_emg[e], t_emg[e] + epoch_s / 3600.0,
                        color="#8e24aa", alpha=0.12)
    ax3.plot(t_emg[:n], emg_rms[:n], color="#ff8a65", linewidth=0.7)
    ax3.set_ylabel("EMG RMS (µV)")
    ax3.set_xlabel("Time (hours)")
    ax3.set_title("EMG tone  (purple = REM epochs — expect atonia)", fontweight="bold")
    ax3.set_xlim(t_ep[0], t_ep[-1])

    fig.suptitle("Overnight Sleep Overview", fontsize=12, fontweight="bold")
    return fig


def plot_spindle_gallery(sig: np.ndarray, spindles: list[dict],
                         fs: int = FS_EEG,
                         n_show: int = 12) -> plt.Figure:
    """Gallery of detected spindle waveforms with sigma-filtered overlay."""
    sos_sigma = _bp(12.0, 15.0, fs)
    sig_filt  = filt(sig, sos_sigma)

    n_show  = min(n_show, len(spindles))
    cols    = 4
    rows    = (n_show + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(14, rows * 2.2))
    axes = axes.flatten() if rows > 1 else axes
    fig.suptitle(f"Sleep Spindles — {len(spindles)} detected  "
                 f"(showing {n_show})", fontweight="bold")

    for i, sp in enumerate(spindles[:n_show]):
        ax   = axes[i]
        s0   = max(0, int((sp["start_s"] - 0.5) * fs))
        s1   = min(len(sig), int((sp["end_s"]   + 0.5) * fs))
        t    = (np.arange(s1 - s0) / fs - 0.5) * 1000   # ms, centred
        ax.plot(t, sig_filt[s0:s1], color="#43a047", linewidth=1.1)
        ax.axvspan(0, sp["duration_s"] * 1000, color="#43a047", alpha=0.08)
        ax.set_title(f"{sp['duration_s']:.2f}s  "
                     f"{sp['peak_freq_hz']:.1f} Hz  "
                     f"{sp['peak_amp_uv']:.0f}µV",
                     fontsize=7.5)
        ax.set_xlabel("ms", fontsize=7)
        ax.axhline(0, color="#ccc", linewidth=0.4)

    for j in range(n_show, len(axes)):
        axes[j].axis("off")
    fig.tight_layout()
    return fig


def plot_slow_wave_gallery(sig: np.ndarray, slow_waves: list[dict],
                           fs: int = FS_EEG, n_show: int = 12) -> plt.Figure:
    """Gallery of detected slow-wave waveforms."""
    sos = _bp(0.5, 4.0, fs)
    sig_filt = filt(sig, sos)

    n_show  = min(n_show, len(slow_waves))
    cols    = 4
    rows    = (n_show + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(14, rows * 2.2))
    axes    = axes.flatten() if rows > 1 else axes
    fig.suptitle(f"Slow Waves — {len(slow_waves)} detected  "
                 f"(showing {n_show})", fontweight="bold")

    for i, sw in enumerate(slow_waves[:n_show]):
        ax  = axes[i]
        pad = 0.5   # seconds of context either side
        s0  = max(0, int((sw["start_s"] - pad) * fs))
        s1  = min(len(sig), int((sw["end_s"] + pad) * fs))
        t   = np.arange(s1 - s0) / fs - pad
        ax.plot(t, sig[s0:s1],      color="#aaaaaa", linewidth=0.6, alpha=0.7)
        ax.plot(t, sig_filt[s0:s1], color="#1e88e5", linewidth=1.2)
        ax.axvspan(0, sw["end_s"] - sw["start_s"], color="#1e88e5", alpha=0.07)
        ax.axhline(0, color="#ccc", linewidth=0.4)
        ax.set_title(f"P-P {sw['pp_uv']:.0f}µV  neg {sw['neg_peak_uv']:.0f}µV",
                     fontsize=7.5)
        ax.set_xlabel("s", fontsize=7)

    for j in range(n_show, len(axes)):
        axes[j].axis("off")
    fig.tight_layout()
    return fig


def plot_rem_episode(eog: np.ndarray, emg: np.ndarray, stages: np.ndarray,
                     rem_bursts: list[dict], fs: int = FS_EEG) -> plt.Figure:
    """
    Show a representative REM episode: EOG trace with burst markers,
    EMG showing atonia, and a short EEG segment.
    """
    # Find the longest continuous REM block
    rem_epochs = np.where(stages == REM)[0]
    if len(rem_epochs) == 0:
        fig, ax = plt.subplots()
        ax.text(0.5, 0.5, "No REM epochs detected", ha="center", va="center",
                transform=ax.transAxes)
        fig.suptitle("REM episode", fontweight="bold")
        return fig

    # Find longest contiguous REM block
    gaps   = np.where(np.diff(rem_epochs) > 1)[0]
    blocks = np.split(rem_epochs, gaps + 1)
    longest = max(blocks, key=len)
    s0 = longest[0] * EPOCH_N
    s1 = min(len(eog), longest[-1] * EPOCH_N + EPOCH_N)
    # Cap at 10 minutes for readability
    s1 = min(s1, s0 + int(fs * 600))

    t = np.arange(s0, s1) / fs / 60.0   # minutes

    sos_eog = _bp(0.5, 5.0, fs)
    sos_emg = _hp(50.0, fs)
    eog_f   = filt(eog[s0:s1], sos_eog)
    emg_f   = filt(emg[s0:s1], sos_emg)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 5), sharex=True)
    fig.suptitle("Representative REM Episode — EOG bursts + EMG atonia",
                 fontweight="bold")

    ax1.plot(t, eog_f, color="#4fc3f7", linewidth=0.5)
    ax1.set_ylabel("EOG (µV)")
    ax1.set_title("EOG  (0.5–5 Hz filtered)", fontsize=9)

    # Mark REM bursts
    burst_t = [r["time_s"] / 60.0 for r in rem_bursts
               if s0 / fs <= r["time_s"] <= s1 / fs]
    if burst_t:
        for bt in burst_t:
            ax1.axvline(bt, color="#8e24aa", linewidth=0.7, alpha=0.7)
    ax1.text(0.01, 0.95, f"{len(burst_t)} REM bursts",
             transform=ax1.transAxes, fontsize=8, color="#8e24aa", va="top")

    ax2.plot(t, emg_f, color="#ff8a65", linewidth=0.5)
    ax2.set_ylabel("EMG HP (µV)")
    ax2.set_xlabel("Time (min into recording)")
    ax2.set_title("EMG (>50 Hz) — should show atonia during REM", fontsize=9)
    fig.tight_layout()
    return fig


def plot_summary(stages: np.ndarray, spindles: list[dict],
                 slow_waves: list[dict], rem_bursts: list[dict],
                 epoch_s: float = 30.0) -> plt.Figure:
    """Summary statistics: stage proportions, spindle/SW density, latencies."""
    fig = plt.figure(figsize=(13, 5))
    gs  = gridspec.GridSpec(1, 3, figure=fig, wspace=0.35)

    # ── Stage pie chart ────────────────────────────────────────────────────────
    ax0 = fig.add_subplot(gs[0])
    counts = {STAGE_LABELS[s]: 0 for s in range(5)}
    for st in stages:
        counts[STAGE_LABELS[st]] += 1
    # Convert to minutes
    minutes = {k: v * epoch_s / 60.0 for k, v in counts.items()}
    labels  = [k for k, v in minutes.items() if v > 0]
    sizes   = [minutes[k] for k in labels]
    colors  = [STAGE_COLORS[[s for s, l in STAGE_LABELS.items() if l == k][0]]
               for k in labels]
    ax0.pie(sizes, labels=labels, colors=colors, autopct="%1.0f%%",
            startangle=90, textprops={"fontsize": 9})
    total_min = sum(sizes)
    ax0.set_title(f"Sleep stage distribution\n(total {total_min:.0f} min "
                  f"= {total_min/60:.1f} h)", fontweight="bold")

    # ── Event bar chart ────────────────────────────────────────────────────────
    ax1 = fig.add_subplot(gs[1])
    # Spindle density (per hour of N2)
    n2_hours  = counts["N2"] * epoch_s / 3600.0
    n3_hours  = counts["N3"] * epoch_s / 3600.0
    rem_hours = counts["REM"] * epoch_s / 3600.0

    spindle_rate  = len(spindles)  / n2_hours  if n2_hours  > 0 else 0
    sw_rate       = len(slow_waves)/ n3_hours  if n3_hours  > 0 else 0
    rem_rate      = len(rem_bursts)/ rem_hours if rem_hours > 0 else 0

    bars  = ["Spindles\n(per h N2)", "Slow waves\n(per h N3)", "REM bursts\n(per h REM)"]
    vals  = [spindle_rate, sw_rate, rem_rate]
    cols  = ["#43a047", "#1e88e5", "#8e24aa"]
    ax1.bar(bars, vals, color=cols, alpha=0.8, width=0.5)
    ax1.set_ylabel("Events / hour")
    ax1.set_title("Event rates", fontweight="bold")
    for i, v in enumerate(vals):
        ax1.text(i, v + 0.5, f"{v:.0f}", ha="center", fontsize=9)

    # ── Latency / architecture stats ──────────────────────────────────────────
    ax2 = fig.add_subplot(gs[2])
    ax2.axis("off")

    # Sleep onset latency (first non-Wake epoch)
    non_wake = np.where(stages != WAKE)[0]
    sol = (non_wake[0] * epoch_s / 60.0) if len(non_wake) else float("nan")

    # REM latency (time from sleep onset to first REM)
    rem_ep = np.where(stages == REM)[0]
    if len(rem_ep) > 0 and len(non_wake) > 0:
        rem_lat = (rem_ep[0] - non_wake[0]) * epoch_s / 60.0
    else:
        rem_lat = float("nan")

    # WASO (Wake After Sleep Onset)
    if len(non_wake) > 0:
        sleep_epochs = stages[non_wake[0]:]
        waso_min = np.sum(sleep_epochs == WAKE) * epoch_s / 60.0
    else:
        waso_min = 0.0

    stats_text = (
        f"Sleep architecture\n"
        f"─────────────────────\n"
        f"Total sleep time:  {(total_min - minutes['Wake']):.0f} min\n"
        f"Sleep onset lat.:  {sol:.0f} min\n"
        f"REM latency:       {rem_lat:.0f} min\n"
        f"WASO:              {waso_min:.0f} min\n"
        f"\n"
        f"Spindles detected: {len(spindles)}\n"
        f"  Median dur:      "
        f"{np.median([s['duration_s'] for s in spindles]):.2f}s\n"
        f"  Mean freq:       "
        f"{np.mean([s['peak_freq_hz'] for s in spindles]):.1f} Hz\n"
        if spindles else
        f"Spindles detected: {len(spindles)}\n"
    ) + (
        f"\nSlow waves:        {len(slow_waves)}\n"
        f"  Median P-P:      "
        f"{np.median([s['pp_uv'] for s in slow_waves]):.0f} µV\n"
        if slow_waves else
        f"\nSlow waves:        {len(slow_waves)}\n"
    ) + f"\nREM bursts:        {len(rem_bursts)}"

    ax2.text(0.05, 0.95, stats_text, transform=ax2.transAxes,
             fontsize=9, va="top", fontfamily="monospace",
             bbox=dict(boxstyle="round,pad=0.5", fc="#f5f5f5", ec="#bbbbbb"))

    fig.suptitle("Sleep Analysis Summary", fontsize=12, fontweight="bold")
    return fig


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sleep analysis — EDF wearable recording")
    parser.add_argument("--edf",    required=True, help="Path to EDF file")
    parser.add_argument("--fitbit", default=None,  help="Path to Fitbit ground-truth JSON")
    parser.add_argument("--out",    default=None,  help="Output directory (default: figures/)")
    parser.add_argument("--fmt",    default="png", choices=["png", "svg", "pdf"])
    args = parser.parse_args()

    # ── Paths ──────────────────────────────────────────────────────────────────
    _here = os.path.dirname(os.path.abspath(__file__))
    out_dir = args.out or os.path.join(_here, "figures")
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.edf))[0]

    def save(fig: plt.Figure, suffix: str):
        path = os.path.join(out_dir, f"{stem}_{suffix}.{args.fmt}")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  -> {path}")

    # ── Load rec_start_epoch from meta.json sidecar ────────────────────────────
    import json
    meta_path = os.path.splitext(args.edf)[0] + "_meta.json"
    rec_start_epoch = None
    if os.path.exists(meta_path):
        with open(meta_path) as _mf:
            rec_start_epoch = json.load(_mf).get("rec_start_epoch")
        print(f"[META] rec_start_epoch = {rec_start_epoch}  ({meta_path})")
    else:
        print(f"[META] No meta.json found at {meta_path} — Fitbit alignment unavailable")

    # ── Load ───────────────────────────────────────────────────────────────────
    data = load_edf(args.edf)

    def _get(label: str) -> np.ndarray | None:
        return data.get(label)

    eeg_c = _get(CH_EEG_CENTRAL)
    eeg_o = _get(CH_EEG_OCCIP)
    eeg_f = _get(CH_EEG_FRONTAL)
    eog   = _get(CH_EOG)
    emg   = _get(CH_EMG)
    accels = [_get(c) for c in CH_ACCEL if _get(c) is not None]

    # Pick best EEG channel for staging: highest sigma std = most spindle variation.
    # EEG_L2 (central) is preferred but falls back if its sigma band is flat.
    candidates = [(CH_EEG_CENTRAL, eeg_c), (CH_EEG_OCCIP, eeg_o), (CH_EEG_FRONTAL, eeg_f)]
    candidates = [(lbl, sig) for lbl, sig in candidates if sig is not None]
    if not candidates:
        print("ERROR: no EEG channels found in EDF")
        sys.exit(1)
    best_lbl, eeg_c = candidates[0]
    best_score = -1.0
    for lbl, sig in candidates:
        # Score = sigma-band std across epochs (higher = more spindle activity = better)
        sos_sigma = _bp(12.0, 15.0, FS_EEG)
        ep_n = EPOCH_N
        n_ep = len(sig) // ep_n
        sigma_pows = []
        for ei in range(min(n_ep, 60)):   # sample first 60 epochs for speed
            seg = sig[ei*ep_n:(ei+1)*ep_n]
            f_s, p_s = welch(filt(seg, sos_sigma), fs=FS_EEG, nperseg=min(ep_n, FS_EEG*4))
            sigma_pows.append(float(np.trapz(p_s[(f_s>=12)&(f_s<=15)],
                                             f_s[(f_s>=12)&(f_s<=15)])))
        score = float(np.std(sigma_pows)) if sigma_pows else 0.0
        print(f"  [CHAN] {lbl:15s}  sigma_std={score:.4f}")
        if score > best_score:
            best_score = score
            best_lbl, eeg_c = lbl, sig
    print(f"  [CHAN] Using {best_lbl} for sleep staging")
    if eog is None or emg is None:
        print("ERROR: EOG or EMG channel missing")
        sys.exit(1)

    # Trim all EEG/EOG/EMG to common length
    n = min(len(eeg_c), len(eog), len(emg))
    if eeg_o is not None: eeg_o = eeg_o[:n]
    if eeg_f is not None: eeg_f = eeg_f[:n]
    eeg_c, eog, emg = eeg_c[:n], eog[:n], emg[:n]

    dur_h = n / FS_EEG / 3600.0
    print(f"\n[INFO] Recording duration: {dur_h:.2f} h  ({n} samples @ {FS_EEG} Hz)")
    print(f"[INFO] Epochs: {n // EPOCH_N} × {EPOCH_S}s\n")

    # ── Feature extraction ────────────────────────────────────────────────────
    print("[STEP 1/5] Computing EEG band powers...")
    bands    = _epoch_bandpowers(eeg_c)
    emg_rms  = _epoch_emg_rms(emg)
    eog_act  = _epoch_eog_activity(eog)

    motion = None
    if len(accels) == 3:
        print("[STEP 1/5] Computing IMU motion energy...")
        # Align IMU to same number of epochs
        min_len = min(len(a) for a in accels)
        accels  = [a[:min_len] for a in accels]
        motion  = _epoch_motion(accels)

    # ── Sleep staging ─────────────────────────────────────────────────────────
    print("[STEP 2/5] Scoring sleep stages...")
    stages = score_hypnogram(bands, emg_rms, eog_act, motion)
    stage_counts = {STAGE_LABELS[s]: int(np.sum(stages == s)) for s in range(5)}
    print("  Stage counts (epochs):", stage_counts)

    # ── Event detection ───────────────────────────────────────────────────────
    print("[STEP 3/5] Detecting sleep spindles...")
    spindles   = detect_spindles(eeg_c)
    print(f"  Found {len(spindles)} spindles")
    for _i, _sp in enumerate(spindles[:10]):
        print(f"  [{_i:2d}] start={_sp['start_s']:.2f}s  end={_sp['end_s']:.2f}s  "
              f"dur={_sp['duration_s']:.2f}s  {_sp['peak_freq_hz']:.1f}Hz  {_sp['peak_amp_uv']:.0f}µV")

    print("[STEP 3/5] Detecting slow waves...")
    eeg_sw     = eeg_o if eeg_o is not None else eeg_c
    slow_waves = detect_slow_waves(eeg_sw)
    print(f"  Found {len(slow_waves)} slow waves")
    for _i, _sw in enumerate(slow_waves[:10]):
        print(f"  [{_i:2d}] start={_sw['start_s']:.2f}s  end={_sw['end_s']:.2f}s  "
              f"pp={_sw['pp_uv']:.0f}µV  neg={_sw['neg_peak_uv']:.0f}µV")

    print("[STEP 3/5] Detecting REM bursts...")
    rem_bursts = detect_rem_bursts(eog, emg, stages)
    print(f"  Found {len(rem_bursts)} REM bursts")

    # ── Rolling band power for overnight panel ─────────────────────────────────
    print("[STEP 4/5] Computing overnight spectral timeseries...")
    t_hours, band_ts = rolling_bandpower_timeseries(eeg_c)

    # ── Figures ───────────────────────────────────────────────────────────────
    print("[STEP 5/5] Generating figures...")

    save(plot_hypnogram(stages, spindles=spindles, slow_waves=slow_waves,
                        rem_bursts=rem_bursts, motion=motion),
         "sleep_hypnogram")

    save(plot_overnight_panel(stages, t_hours, band_ts, eog, emg),
         "sleep_overnight_panel")

    if spindles:
        save(plot_spindle_gallery(eeg_c, spindles), "sleep_spindles")
    else:
        print("  (no spindles detected — skipping gallery)")

    if slow_waves:
        save(plot_slow_wave_gallery(eeg_sw, slow_waves), "sleep_slow_waves")
    else:
        print("  (no slow waves detected — skipping gallery)")

    save(plot_rem_episode(eog, emg, stages, rem_bursts), "sleep_rem_episode")
    save(plot_summary(stages, spindles, slow_waves, rem_bursts),  "sleep_summary")

    # ── Fitbit comparison (optional) ───────────────────────────────────────────
    if args.fitbit:
        if rec_start_epoch is None:
            print("[FITBIT] No rec_start_epoch — cannot align Fitbit data. "
                  "Make sure the meta.json sidecar exists alongside the EDF.")
        else:
            import json as _json
            with open(args.fitbit) as _ff:
                gt = _json.load(_ff)
            n_ep = len(stages)
            fitbit_stages = parse_fitbit(gt, rec_start_epoch, n_ep)
            if fitbit_stages is None:
                print("[FITBIT] segments list is empty — skipping comparison.")
            else:
                stats = agreement_stats(stages, fitbit_stages)
                print(f"[FITBIT] Accuracy {stats['accuracy']*100:.0f}%  "
                      f"k={stats['kappa']:.2f}")
                for name, v in stats["per_stage"].items():
                    print(f"         {name:6s}  recall {v['recall']*100:.0f}%  "
                          f"precision {v['precision']*100:.0f}%")
                save(plot_comparison(stages, fitbit_stages, stats=stats),
                     "sleep_vs_fitbit")
                save(plot_hypnogram_overlay(stages, fitbit_stages),
                     "sleep_vs_fitbit_overlay")

    print(f"\nDone. All figures saved to {out_dir}/")


if __name__ == "__main__":
    main()
