"""
hr_from_edf.py — HR estimation from a single ADS1299 EDF recording
====================================================================
Tries eight approaches on the ECG channel (CH2 = EMG_far):

  Method A  — simple peak detection on HP-filtered signal (what the live viewer does now)
  Method B  — Pan-Tompkins pipeline (bandpass → derivative → square → moving-average)
  Method C  — Pan-Tompkins + artifact gating using EOG (blink) and IMU/EEG motion mask
  Method D  — Autocorrelation period estimation (single BPM; robust to weak/noisy signal)
  Method E  — Template cross-correlation (average QRS template from Method A → xcorr peaks, 85th pct threshold)
  Method F  — Welch PSD spectral (dominant HR frequency from power spectrum; single BPM)
  Method G  — CWT wavelet (Ricker wavelet across QRS scales; multi-scale power → peak detect)
  Method H  — Hilbert envelope (8–20 Hz BP → Hilbert magnitude envelope → peak detect)

Prints BPM estimate from each method and saves a figure showing the waveform,
detected R-peaks, artifact windows, and a per-method summary panel.

Usage:
    python hr_from_edf.py recordings/eeg_20260408_204723.edf
    python hr_from_edf.py recordings/eeg_20260408_204723.edf --show

    python .\hr_from_edf.py ..\..\recordings\eeg_20260409_225425.edf --show   
"""

import argparse
import os
import sys

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from scipy.signal import butter, filtfilt, iirnotch, find_peaks

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', '..'))
from eeg_motion import (
    imu_dynamic_accel_mask, detect_eeg_jump_mask,
    MOTION_THRESHOLD_MG, MOTION_HOLDOFF_S, IMU_GRAVITY_ALPHA, IMU_DYNAMIC_ALPHA,
)

# ── Plot style (matches the rest of the project) ──────────────────────────────
plt.rcParams.update({
    "figure.facecolor":  "#0d1117",
    "axes.facecolor":    "#161b22",
    "axes.edgecolor":    "#30363d",
    "axes.labelcolor":   "#8b949e",
    "axes.titlecolor":   "#e6edf3",
    "xtick.color":       "#8b949e",
    "ytick.color":       "#8b949e",
    "text.color":        "#e6edf3",
    "grid.color":        "#21262d",
    "grid.linewidth":    0.6,
    "legend.facecolor":  "#161b22",
    "legend.edgecolor":  "#30363d",
    "legend.labelcolor": "#e6edf3",
    "font.size":         9,
})

C_ECG    = "#58a6ff"    # ECG waveform — blue
C_PEAK   = "#3fb950"    # detected R-peaks — green
C_ART    = "#f85149"    # artifact mask — red
C_PT     = "#d2a8ff"    # Pan-Tompkins envelope — purple
C_EOG    = "#4fc3f7"    # EOG channel — cyan
C_MOTION = "#ffa726"    # motion metric — orange
C_TEMPL  = "#f48fb1"    # template cross-correlation — pink
C_AUTOCR = "#80cbc4"    # autocorrelation — teal
C_SPECTR = "#ffca28"    # spectral PSD — amber
C_WAVELT = "#26a69a"    # CWT wavelet — teal-green
C_HILBRT = "#ab47bc"    # Hilbert envelope — purple

FS = 250.0   # ADS1299 sample rate


# ── Signal processing helpers ─────────────────────────────────────────────────

def hp_filter(x: np.ndarray, fs: float, fc: float = 0.5) -> np.ndarray:
    """2nd-order Butterworth HP — removes DC / electrode drift."""
    b, a = butter(2, fc / (fs / 2), btype="high")
    return filtfilt(b, a, x)


def bp_filter(x: np.ndarray, fs: float, lo: float = 5.0, hi: float = 20.0) -> np.ndarray:
    """4th-order Butterworth bandpass — isolates QRS complex energy."""
    nyq = fs / 2
    b, a = butter(4, [lo / nyq, hi / nyq], btype="band")
    return filtfilt(b, a, x)


def notch_filter(x: np.ndarray, fs: float, f0: float = 60.0, q: float = 30.0) -> np.ndarray:
    b, a = iirnotch(f0 / (fs / 2), q)
    return filtfilt(b, a, x)


def pan_tompkins_envelope(ecg_hp: np.ndarray, fs: float) -> np.ndarray:
    """
    Compute the Pan-Tompkins decision signal from a HP-filtered ECG.
    Returns the moving-window-integrated envelope (all non-negative).
    Steps: bandpass → derivative → square → moving average
    """
    bp   = bp_filter(ecg_hp, fs)                     # bandpass 5–20 Hz
    diff = np.gradient(bp)                            # derivative (amplifies QRS slope)
    sq   = diff ** 2                                  # squaring (all positive, boosts peaks)
    win  = max(1, int(0.150 * fs))                    # 150 ms integration window
    env  = np.convolve(sq, np.ones(win) / win, mode="same")   # moving average
    return env


def detect_peaks_simple(ecg_hp: np.ndarray, fs: float) -> np.ndarray:
    """
    Current live-viewer approach: find_peaks on HP-filtered signal with a
    percentile-based height threshold.
    """
    min_dist = int(fs * 0.33)   # ~180 BPM max
    rng      = np.percentile(ecg_hp, 95) - np.percentile(ecg_hp, 5)
    height   = np.median(ecg_hp) + 0.40 * rng
    peaks, _ = find_peaks(ecg_hp, height=height, distance=min_dist)
    return peaks


def detect_peaks_pt(env: np.ndarray, fs: float) -> np.ndarray:
    """
    Pan-Tompkins peak detection on the integrated envelope.
    Uses a simple 3-level adaptive threshold seeded from the first half of the signal.
    """
    min_dist = int(fs * 0.33)
    # Seed threshold from signal level in first half
    half    = len(env) // 2 if len(env) > 1 else len(env)
    spki    = np.mean(env[:half]) * 3.0   # signal peak estimate
    npki    = np.mean(env[:half]) * 0.5   # noise peak estimate
    thresh1 = npki + 0.25 * (spki - npki)

    # Find all candidate peaks first, then threshold adaptively
    candidates, _ = find_peaks(env, distance=min_dist)
    accepted = []
    for p in candidates:
        if env[p] >= thresh1:
            accepted.append(p)
            spki = 0.125 * env[p] + 0.875 * spki
        else:
            npki = 0.125 * env[p] + 0.875 * npki
        thresh1 = npki + 0.25 * (spki - npki)

    return np.array(accepted, dtype=int)


def autocorr_hr(ecg_hp: np.ndarray, fs: float,
                lo_bpm: float = 40.0, hi_bpm: float = 180.0) -> tuple:
    """
    Estimate heart rate from the dominant lag in the normalised autocorrelation.

    Doesn't require explicit peak detection — finds the dominant period directly
    from signal periodicity.  Works well on weak or noisy ECG where threshold-based
    peak detection struggles.

    Returns
    -------
    bpm       : float       — estimated heart rate (NaN if estimation failed)
    lag       : int         — dominant lag in samples
    ac        : np.ndarray  — full normalised autocorrelation (positive lags)
    """
    x = ecg_hp - ecg_hp.mean()
    std = x.std()
    if std < 1e-12:
        return np.nan, 0, np.zeros(len(ecg_hp))
    x = x / std
    ac = np.correlate(x, x, mode='full')[len(x) - 1:]   # positive lags only
    ac = ac / ac[0]                                       # normalise: lag-0 = 1
    lo_lag = max(1, int(60.0 / hi_bpm * fs))
    hi_lag = min(len(ac) - 1, int(60.0 / lo_bpm * fs))
    if lo_lag >= hi_lag:
        return np.nan, 0, ac
    region  = ac[lo_lag : hi_lag + 1]
    peak_off = int(np.argmax(region))
    lag      = lo_lag + peak_off
    # Parabolic sub-sample refinement to reduce quantisation error
    if 0 < peak_off < len(region) - 1:
        a, b, c = region[peak_off - 1], region[peak_off], region[peak_off + 1]
        denom = a - 2 * b + c
        lag_f = lag + (0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0)
    else:
        lag_f = float(lag)
    bpm = 60.0 * fs / lag_f
    return bpm, lag, ac


def detect_peaks_template(ecg_hp: np.ndarray, fs: float,
                          seed_peaks: np.ndarray,
                          half_win_ms: float = 150.0) -> np.ndarray:
    """
    Template cross-correlation peak detector.

    Builds an average QRS template from seed_peaks (align → normalise → mean),
    cross-correlates it with ecg_hp, then finds peaks in the correlation output.
    Improves SNR vs simple threshold detection for weak signals — the averaging
    suppresses noise while preserving QRS morphology.

    Parameters
    ----------
    ecg_hp      : HP-filtered ECG signal
    seed_peaks  : initial R-peak indices used to build the template (e.g. from Method A)
    half_win_ms : half-window around each seed peak for template extraction (ms)

    Returns
    -------
    peaks : np.ndarray — refined R-peak indices (falls back to seed_peaks if template build fails)
    """
    half_win = int(half_win_ms * fs / 1000)
    valid = seed_peaks[
        (seed_peaks > half_win) & (seed_peaks < len(ecg_hp) - half_win)
    ]
    if len(valid) < 2:
        return seed_peaks
    snippets = np.array([ecg_hp[p - half_win : p + half_win + 1] for p in valid])
    # Normalise each snippet by its own std to avoid amplitude bias in averaging
    stds = snippets.std(axis=1, keepdims=True)
    stds[stds < 1e-12] = 1.0
    snippets = snippets / stds
    template  = snippets.mean(axis=0)
    template -= template.mean()
    tnorm = np.linalg.norm(template)
    if tnorm < 1e-12:
        return seed_peaks
    template = template / tnorm
    # Cross-correlate template with the full normalised signal
    sig_norm = ecg_hp - ecg_hp.mean()
    sig_std  = sig_norm.std()
    if sig_std < 1e-12:
        return seed_peaks
    sig_norm = sig_norm / sig_std
    xcorr = np.correlate(sig_norm, template, mode='same')
    # Peak detection on the correlation output
    min_dist = int(fs * 0.33)
    height   = np.percentile(xcorr, 85)   # 85th percentile — tighter to suppress false peaks on noisy signals
    peaks, _ = find_peaks(xcorr, height=height, distance=min_dist)
    return peaks if len(peaks) >= 2 else seed_peaks


def spectral_hr(ecg_hp: np.ndarray, fs: float,
                lo_bpm: float = 40.0, hi_bpm: float = 180.0) -> float:
    """
    Welch PSD — find dominant HR frequency directly from the ECG power spectrum.
    Returns BPM (NaN if estimation failed).  Orthogonal to autocorrelation (Method D):
    autocorrelation works in the lag domain, this works in the frequency domain.
    """
    from scipy.signal import welch
    nperseg = min(len(ecg_hp), int(fs * 8))   # 8-second windows for good freq resolution
    f, psd  = welch(ecg_hp, fs=fs, nperseg=nperseg)
    lo_hz   = lo_bpm / 60.0
    hi_hz   = hi_bpm / 60.0
    mask    = (f >= lo_hz) & (f <= hi_hz)
    if not mask.any():
        return np.nan
    peak_f = f[mask][np.argmax(psd[mask])]
    return peak_f * 60.0


def detect_peaks_wavelet(ecg_hp: np.ndarray, fs: float) -> np.ndarray:
    """
    CWT (Ricker / Mexican-hat wavelet) peak detector.

    Sums CWT magnitude across scales spanning the QRS complex width (~60–180 ms
    at 250 Hz = 15–45 samples), producing a multi-scale power trace.  Peaks in
    this trace correspond to QRS complexes regardless of exact amplitude — useful
    for low-SNR signals where single-scale thresholding struggles.
    """
    from scipy.signal import cwt, ricker
    # QRS complex ~60–150 ms wide → at 250 Hz = 15–37 samples
    widths   = np.arange(8, 30)
    cwtm     = cwt(ecg_hp, ricker, widths)
    power    = np.sum(np.abs(cwtm), axis=0)
    min_dist = int(fs * 0.33)
    height   = np.percentile(power, 85)
    peaks, _ = find_peaks(power, height=height, distance=min_dist)
    return peaks


def detect_peaks_hilbert(ecg_hp: np.ndarray, fs: float) -> np.ndarray:
    """
    Hilbert envelope peak detector.

    Narrow-bandpasses the ECG at QRS derivative frequencies (8–20 Hz), takes
    the Hilbert transform magnitude as the instantaneous amplitude envelope,
    smooths with a 100 ms moving average, then finds peaks.  Orthogonal to
    Pan-Tompkins: same frequency range but Hilbert envelope vs squared-derivative.
    """
    from scipy.signal import hilbert
    nyq = fs / 2
    b, a     = butter(4, [8.0 / nyq, 20.0 / nyq], btype="band")
    filtered  = filtfilt(b, a, ecg_hp)
    envelope  = np.abs(hilbert(filtered))
    win       = max(1, int(0.10 * fs))   # 100 ms smoothing
    envelope  = np.convolve(envelope, np.ones(win) / win, mode="same")
    min_dist  = int(fs * 0.33)
    height    = np.percentile(envelope, 85)
    peaks, _  = find_peaks(envelope, height=height, distance=min_dist)
    return peaks


def rr_to_bpm(peaks: np.ndarray, fs: float) -> tuple:
    """
    Convert peak sample indices → (rr_ms, bpm) arrays.
    Returns NaN for intervals that are physiologically implausible (< 30 or > 220 BPM).
    """
    if len(peaks) < 2:
        return np.array([]), np.array([])
    rr_s  = np.diff(peaks) / fs
    bpm   = 60.0 / rr_s
    # Reject implausible beats
    mask  = (bpm > 30) & (bpm < 220)
    rr_s  = np.where(mask, rr_s, np.nan)
    bpm   = np.where(mask, bpm, np.nan)
    return rr_s * 1000, bpm   # (rr_ms, bpm)


def rr_outlier_filter(peaks: np.ndarray, fs: float, tol: float = 0.20) -> np.ndarray:
    """
    Remove R-peaks whose RR interval deviates > tol (20%) from the running median.
    Returns a cleaned peak array.
    """
    if len(peaks) < 3:
        return peaks
    rr = np.diff(peaks).astype(float)
    med = np.median(rr)
    keep = [peaks[0]]
    for i, p in enumerate(peaks[1:], start=1):
        if abs(rr[i - 1] - med) / med < tol:
            keep.append(p)
            med = 0.9 * med + 0.1 * rr[i - 1]   # slow-follow
    return np.array(keep, dtype=int)


# ── Artifact detection ────────────────────────────────────────────────────────

def detect_blink_mask(eog: np.ndarray, fs: float,
                      threshold_uv: float = 300.0, pad_ms: float = 150.0) -> np.ndarray:
    """
    Boolean mask: True where a blink artifact is present in the EOG channel.
    Blinks are large, fast deflections — detect as |EOG| > threshold_uv.
    Pads each detected region by pad_ms on each side.
    """
    mask = np.abs(eog) > threshold_uv
    pad  = int(pad_ms * fs / 1000)
    # Dilate: pad each True run
    dilated = np.copy(mask)
    for i in np.where(mask)[0]:
        lo = max(0, i - pad)
        hi = min(len(mask), i + pad)
        dilated[lo:hi] = True
    return dilated


def gate_peaks(peaks: np.ndarray, artifact_mask: np.ndarray) -> np.ndarray:
    """Remove peaks that fall inside an artifact window."""
    if len(peaks) == 0:
        return peaks
    return peaks[~artifact_mask[peaks]]


def summarise_bpm(peaks: np.ndarray, fs: float, label: str):
    """Print a tidy BPM summary for a set of detected peaks."""
    if len(peaks) < 2:
        print(f"  {label}: insufficient peaks ({len(peaks)}) — can't estimate HR")
        return
    _, bpm = rr_to_bpm(peaks, fs)
    valid  = bpm[~np.isnan(bpm)]
    if len(valid) == 0:
        print(f"  {label}: all intervals implausible")
        return
    print(f"  {label}: {len(peaks)} peaks  |  mean={np.mean(valid):.1f}  "
          f"std={np.std(valid):.1f}  range=[{np.min(valid):.0f}–{np.max(valid):.0f}] BPM")


# ── Plotting ──────────────────────────────────────────────────────────────────

def _ax(ax, title="", xlabel="", ylabel=""):
    ax.set_title(title, fontsize=9, fontweight="bold", pad=4)
    ax.set_xlabel(xlabel, fontsize=8)
    ax.set_ylabel(ylabel, fontsize=8)
    for sp in ax.spines.values():
        sp.set_edgecolor("#30363d")
    ax.grid(True)


def shade_artifacts(ax, artifact_mask: np.ndarray, t: np.ndarray):
    """Shade artifact windows red."""
    in_art = False
    t_start = 0.0
    for i, m in enumerate(artifact_mask):
        if m and not in_art:
            t_start = t[i]
            in_art = True
        elif not m and in_art:
            ax.axvspan(t_start, t[i], color=C_ART, alpha=0.15, lw=0)
            in_art = False
    if in_art:
        ax.axvspan(t_start, t[-1], color=C_ART, alpha=0.15, lw=0)


def make_figure(
    t: np.ndarray,
    ecg_hp: np.ndarray,
    pt_env: np.ndarray,
    eog_hp: np.ndarray,
    motion_metric: np.ndarray,
    t_motion: np.ndarray,
    motion_threshold: float,
    motion_unit: str,
    blink_mask: np.ndarray,
    motion_mask: np.ndarray,
    art_mask: np.ndarray,
    peaks_a: np.ndarray,
    peaks_b: np.ndarray,
    peaks_c: np.ndarray,
    peaks_d: np.ndarray,
    peaks_e: np.ndarray,
    peaks_g: np.ndarray,
    peaks_h: np.ndarray,
    bpm_d: float,
    bpm_f: float,
    lag_d: int,
    ac: np.ndarray,
    fs: float,
) -> plt.Figure:
    fig, axes = plt.subplots(
        6, 1, figsize=(13, 17),
        gridspec_kw={"height_ratios": [3, 1.2, 1.2, 1.2, 1.2, 2.5], "hspace": 0.48},
    )
    fig.suptitle(
        "ECG HR Estimation — ADS1299 EDF  (CH2 = EMG_far / ECG electrode)",
        fontsize=12, fontweight="bold", y=0.99,
    )

    # ── Panel 0: HP-filtered ECG + all three peak sets ───────────────────────
    ax = axes[0]
    ax.plot(t, ecg_hp, color=C_ECG, lw=0.9, label="ECG (HP-filtered, z-scored)")
    ecg_z = (ecg_hp - ecg_hp.mean()) / (ecg_hp.std() or 1)

    # re-plot z-scored so peaks are comparable
    ax.cla()
    ax.plot(t, ecg_z, color=C_ECG, lw=0.9, label="ECG (HP, z-scored)")

    ms = 70
    if len(peaks_a): ax.scatter(t[peaks_a], ecg_z[peaks_a], color="#ffa726", s=ms, zorder=5,
                                marker="v", label=f"A — simple ({len(peaks_a)} peaks)")
    if len(peaks_b): ax.scatter(t[peaks_b], ecg_z[peaks_b], color=C_PEAK,   s=ms, zorder=5,
                                marker="^", label=f"B — Pan-Tompkins ({len(peaks_b)} peaks)")
    if len(peaks_c): ax.scatter(t[peaks_c], ecg_z[peaks_c], color="#d2a8ff", s=ms, zorder=5,
                                marker="D", label=f"C — PT + artifact gate ({len(peaks_c)} peaks)")
    if len(peaks_d): ax.scatter(t[peaks_d], ecg_z[peaks_d], color=C_AUTOCR, s=ms, zorder=5,
                                marker="o", label=f"D — autocorr peaks ({len(peaks_d)} peaks)")
    if len(peaks_e): ax.scatter(t[peaks_e], ecg_z[peaks_e], color=C_TEMPL,  s=ms, zorder=5,
                                marker="s", label=f"E — template xcorr ({len(peaks_e)} peaks)")
    if len(peaks_g): ax.scatter(t[peaks_g], ecg_z[peaks_g], color=C_WAVELT, s=ms, zorder=5,
                                marker="P", label=f"G — CWT wavelet ({len(peaks_g)} peaks)")
    if len(peaks_h): ax.scatter(t[peaks_h], ecg_z[peaks_h], color=C_HILBRT, s=ms, zorder=5,
                                marker="X", label=f"H — Hilbert env ({len(peaks_h)} peaks)")

    shade_artifacts(ax, art_mask, t)
    _ax(ax, title="ECG waveform + detected R-peaks (all 8 methods)",
        xlabel="Time (s)", ylabel="Amplitude (z-score)")
    ax.legend(loc="upper right", fontsize=7, ncol=3)

    # ── Panel 1: Pan-Tompkins envelope ───────────────────────────────────────
    ax = axes[1]
    ax.plot(t, pt_env / pt_env.max(), color=C_PT, lw=0.9, label="PT envelope (normalised)")
    if len(peaks_b):
        ax.scatter(t[peaks_b], pt_env[peaks_b] / pt_env.max(), color=C_PEAK, s=50, zorder=5, marker="^")
    shade_artifacts(ax, art_mask, t)
    _ax(ax, title="Pan-Tompkins decision signal  (Method B)", xlabel="Time (s)", ylabel="Normalised")
    ax.legend(fontsize=8)

    # ── Panel 2: Autocorrelation (Method D) ──────────────────────────────────
    ax = axes[2]
    lo_lag = int(60.0 / 180.0 * fs)
    hi_lag = int(60.0 / 40.0 * fs)
    t_ac   = np.arange(len(ac)) / fs
    # Only show the physiological search range
    ax.plot(t_ac[lo_lag:hi_lag], ac[lo_lag:hi_lag], color=C_AUTOCR, lw=1.0,
            label="normalised autocorrelation (physiological range)")
    ax.axhline(0, color="#555555", lw=0.5, ls="--")
    lag_secs = len(ac) / fs   # just for axis
    if not np.isnan(bpm_d):
        lag_d_t = lag_d / fs
        ax.axvline(lag_d_t, color=C_AUTOCR, lw=1.5, ls="--",
                   label=f"dominant lag = {lag_d/fs*1000:.0f} ms  →  {bpm_d:.1f} BPM")
        ax.scatter([lag_d_t], [ac[min(lag_d, len(ac)-1)]], color=C_AUTOCR, s=80, zorder=5)
    _ax(ax, title="Autocorrelation — dominant RR lag  (Method D)",
        xlabel="Lag (s)", ylabel="r")
    ax.legend(fontsize=8)

    # ── Panel 3: EOG (blink detection) ───────────────────────────────────────
    ax = axes[3]
    ax.plot(t, eog_hp, color=C_EOG, lw=0.8, label="EOG (HP-filtered)")
    ax.fill_between(t, 0, blink_mask.astype(float) * eog_hp.max(),
                    where=blink_mask, color=C_ART, alpha=0.25, label="blink mask")
    _ax(ax, title="EOG channel — blink artifact detection", xlabel="Time (s)", ylabel="µV")
    ax.legend(fontsize=8)

    # ── Panel 4: Motion metric ────────────────────────────────────────────────
    ax = axes[4]
    ax.plot(t_motion, motion_metric, color=C_MOTION, lw=0.8, label=f"smoothed dynamic accel ({motion_unit})")
    ax.axhline(motion_threshold, color=C_ART, lw=1.0, ls="--",
               label=f"threshold ({motion_threshold:.0f} {motion_unit})")
    shade_artifacts(ax, motion_mask, t)
    _ax(ax, title="IMU motion artifact metric — dynamic accel (raw − gravity estimate, low-pass smoothed)",
        xlabel="Time (s)", ylabel=motion_unit)
    ax.legend(fontsize=8)
    ax.set_yscale("log")

    # ── Panel 5: BPM per-interval bar chart ──────────────────────────────────
    ax = axes[5]
    bar_w = 0.20
    # Peak-based methods — bars per RR interval
    peak_methods = [
        ("A  simple",         peaks_a, "#ffa726"),
        ("B  Pan-Tompkins",   peaks_b, C_PEAK),
        ("C  PT + gating",    peaks_c, "#d2a8ff"),
        ("E  template xcorr", peaks_e, C_TEMPL),
        ("G  CWT wavelet",    peaks_g, C_WAVELT),
        ("H  Hilbert env",    peaks_h, C_HILBRT),
    ]
    n_methods = len(peak_methods)
    for mi, (label, peaks, color) in enumerate(peak_methods):
        if len(peaks) < 2:
            continue
        _, bpm = rr_to_bpm(peaks, fs)
        t_mid = (t[peaks[:-1]] + t[peaks[1:]]) / 2
        offset = (mi - (n_methods - 1) / 2) * bar_w * 0.45
        x_pos  = t_mid + offset
        valid  = ~np.isnan(bpm)
        ax.bar(x_pos[valid], bpm[valid], width=bar_w * 0.40, color=color,
               alpha=0.75, label=label, zorder=3)
        if valid.any():
            ax.axhline(np.mean(bpm[valid]), color=color, lw=1.2, ls="--", alpha=0.7)

    # Method D — single autocorrelation BPM estimate shown as a solid horizontal line
    if not np.isnan(bpm_d):
        ax.axhline(bpm_d, color=C_AUTOCR, lw=2.0, ls="-",
                   label=f"D  autocorr  ({bpm_d:.1f} BPM)", zorder=4)
        ax.text(t[-1] * 0.01, bpm_d + 1.5, f"{bpm_d:.1f}", color=C_AUTOCR, fontsize=8)
    # Method F — spectral BPM estimate as a dashed horizontal line
    if not np.isnan(bpm_f):
        ax.axhline(bpm_f, color=C_SPECTR, lw=2.0, ls="--",
                   label=f"F  spectral  ({bpm_f:.1f} BPM)", zorder=4)
        ax.text(t[-1] * 0.01, bpm_f + 1.5, f"{bpm_f:.1f}", color=C_SPECTR, fontsize=8)

    # Reference range
    ax.axhspan(70, 80, color="#ffffff", alpha=0.05, label="expected 70–80 BPM")
    ax.set_ylim(30, 160)
    _ax(ax, title="Instantaneous BPM per RR interval  (D = single autocorr estimate, dashed = mean)",
        xlabel="Time (s)", ylabel="BPM")
    ax.legend(fontsize=7, loc="upper right", ncol=2)

    return fig


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="HR estimation from ADS1299 EDF")
    parser.add_argument("edf", help="Path to the EDF file")
    parser.add_argument("--show", action="store_true", help="Show interactive figure")
    parser.add_argument("--outdir", default=None,
                        help="Directory for saved figure (default: figures/ next to script)")
    args = parser.parse_args()

    # ── Load EDF ─────────────────────────────────────────────────────────────
    try:
        import pyedflib
    except ImportError:
        sys.exit("[ERROR] pyedflib not installed — run: pip install pyedflib")

    f = pyedflib.EdfReader(args.edf)
    all_labels = list(f.getSignalLabels())
    # Split channels by sample rate: EEG at 250 Hz, IMU at 25 Hz
    eeg_fs   = float(f.getSampleFrequency(0))
    eeg_idxs = [i for i in range(f.signals_in_file)
                if f.getSampleFrequency(i) == eeg_fs]
    imu_idxs = [i for i in range(f.signals_in_file)
                if f.getSampleFrequency(i) != eeg_fs]
    n      = f.getNSamples()[eeg_idxs[0]]
    fs     = eeg_fs
    sigs   = np.array([f.readSignal(i) for i in eeg_idxs])   # (8, N)
    labels = [all_labels[i] for i in eeg_idxs]

    # Load IMU channels (ACCEL_X, ACCEL_Y, ACCEL_Z) if present
    imu_data     = None
    imu_fs_actual = None
    if imu_idxs:
        imu_labels_in = [all_labels[i] for i in imu_idxs]
        imu_fs_actual = float(f.getSampleFrequency(imu_idxs[0]))
        imu_sigs = {all_labels[i]: f.readSignal(i) for i in imu_idxs}
        # Need ACCEL_X/Y/Z for the physics-based motion detection
        if all(k in imu_sigs for k in ("ACCEL_X", "ACCEL_Y", "ACCEL_Z")):
            imu_data = np.column_stack([
                imu_sigs["ACCEL_X"],
                imu_sigs["ACCEL_Y"],
                imu_sigs["ACCEL_Z"],
            ])   # (N_imu, 3)  in mg
            print(f"  IMU channels loaded: {imu_labels_in} @ {imu_fs_actual:.0f} Hz  "
                  f"({len(imu_data)} samples)")
        else:
            print(f"  IMU channels present ({imu_labels_in}) but ACCEL_X/Y/Z missing — "
                  f"falling back to EEG-channel jump detection")
    f._close()

    print(f"\nLoaded: {os.path.basename(args.edf)}")
    print(f"  {n} samples @ {fs:.0f} Hz = {n/fs:.1f} s")
    print(f"  Channels: {labels}")

    t = np.arange(n) / fs

    # Channel indices (from EDF label order set in eeg_stream_pg.py save code)
    # ["EOG","EMG_far","EMG_near","EEG_L1","EEG_L2","EEG_L3","SRB1","DRL"]
    CH_EOG = 0
    CH_ECG = 1   # EMG_far = ECG electrode

    raw_ecg = sigs[CH_ECG]
    raw_eog = sigs[CH_EOG]

    # ── Preprocessing ────────────────────────────────────────────────────────
    print("\n[1] Preprocessing...")
    # HP removes large DC offset (electrode drift, up to ~20 mV seen in this file)
    ecg_hp  = hp_filter(raw_ecg, fs, fc=0.5)
    ecg_hp  = notch_filter(ecg_hp, fs)

    eog_hp  = hp_filter(raw_eog, fs, fc=0.5)
    eog_hp  = notch_filter(eog_hp, fs)

    print(f"  ECG raw:  mean={raw_ecg.mean():.0f} µV  std={raw_ecg.std():.0f} µV")
    print(f"  ECG (HP): mean={ecg_hp.mean():.2f} µV  std={ecg_hp.std():.2f} µV")

    # ── Pan-Tompkins envelope ─────────────────────────────────────────────────
    pt_env = pan_tompkins_envelope(ecg_hp, fs)

    # ── Artifact detection ────────────────────────────────────────────────────
    print("\n[2] Detecting artifacts...")
    blink_mask  = detect_blink_mask(eog_hp, fs, threshold_uv=300.0, pad_ms=150.0)

    # Motion: IMU dynamic-accel pipeline (same as live code) supplemented by EEG
    # channel jump detection (catches electrode pop that the IMU can't see).
    # Falls back to EEG-channel jumps alone for older recordings without IMU data.
    if imu_data is not None:
        imu_mask, motion_metric, t_motion = imu_dynamic_accel_mask(
            imu_data,
            imu_fs=imu_fs_actual,
            eeg_n=n,
            eeg_fs=fs,
            threshold_mg=MOTION_THRESHOLD_MG,
            holdoff_s=MOTION_HOLDOFF_S,
            gravity_alpha=IMU_GRAVITY_ALPHA,
            dynamic_alpha=IMU_DYNAMIC_ALPHA,
        )
        # Supplement: EEG-channel electrode-pop detection (OR with IMU mask)
        eeg_jump_mask = detect_eeg_jump_mask(sigs.T, fs, jump_uv=500.0, pad_ms=200.0)
        motion_mask   = imu_mask | eeg_jump_mask
        motion_threshold = MOTION_THRESHOLD_MG
        motion_unit      = "mg"
        print(f"  Motion detection: IMU dynamic-accel + EEG electrode-pop  "
              f"(IMU threshold={MOTION_THRESHOLD_MG:.0f} mg, "
              f"holdoff={MOTION_HOLDOFF_S*1000:.0f} ms, EEG jump=500 µV/sample)")
    else:
        # Fallback: EEG-channel jumps only (no IMU channels in this EDF)
        motion_mask   = detect_eeg_jump_mask(sigs.T, fs, jump_uv=5000.0, pad_ms=200.0)
        motion_metric = np.abs(np.diff(sigs, axis=1)).max(axis=0)   # (N-1,)
        t_motion      = t[1:]
        motion_threshold = 5000.0
        motion_unit      = "µV/sample"
        print(f"  Motion detection: EEG-channel jump fallback (no IMU channels in EDF)")

    art_mask = blink_mask | motion_mask

    n_blink  = int(blink_mask.sum())
    n_motion = int(motion_mask.sum())
    n_art    = int(art_mask.sum())
    print(f"  Blink  mask: {n_blink} samples ({100*n_blink/n:.1f}%)")
    print(f"  Motion mask: {n_motion} samples ({100*n_motion/n:.1f}%)")
    print(f"  Combined:    {n_art} samples ({100*n_art/n:.1f}%) gated out")

    # ── Method A: simple peak detection (current live-viewer approach) ────────
    print("\n[3] Method A — simple threshold peak detection...")
    peaks_a = detect_peaks_simple(ecg_hp, fs)
    peaks_a = rr_outlier_filter(peaks_a, fs, tol=0.20)
    summarise_bpm(peaks_a, fs, "A  simple")

    # ── Method B: Pan-Tompkins ────────────────────────────────────────────────
    print("\n[4] Method B — Pan-Tompkins...")
    peaks_b = detect_peaks_pt(pt_env, fs)
    peaks_b = rr_outlier_filter(peaks_b, fs, tol=0.20)
    summarise_bpm(peaks_b, fs, "B  Pan-Tompkins")

    # ── Method C: Pan-Tompkins + artifact gating ──────────────────────────────
    print("\n[5] Method C — Pan-Tompkins + artifact gating...")
    peaks_c = gate_peaks(peaks_b, art_mask)
    peaks_c = rr_outlier_filter(peaks_c, fs, tol=0.20)
    summarise_bpm(peaks_c, fs, "C  PT + gating")

    # ── Method D: Autocorrelation HR estimate ─────────────────────────────────
    print("\n[6] Method D — Autocorrelation period estimation...")
    bpm_d, lag_d, ac = autocorr_hr(ecg_hp, fs)
    # Synthesise peaks at the estimated period for the figure (not used for BPM estimate)
    if not np.isnan(bpm_d) and lag_d > 0:
        # Place synthetic peaks starting from the first strong peak in Method A
        # (or from zero if A found nothing) spaced by lag_d samples
        t0 = int(peaks_a[0]) if len(peaks_a) > 0 else lag_d
        peaks_d = np.arange(t0, n, lag_d, dtype=int)
        peaks_d = peaks_d[peaks_d < n]
        print(f"  D  autocorr: {bpm_d:.1f} BPM  (dominant lag = {lag_d} samples = "
              f"{lag_d/fs*1000:.0f} ms)")
    else:
        peaks_d = np.array([], dtype=int)
        print(f"  D  autocorr: estimation failed")

    # ── Method E: Template cross-correlation ──────────────────────────────────
    print("\n[7] Method E — Template cross-correlation (seeded from Method A)...")
    peaks_e = detect_peaks_template(ecg_hp, fs, peaks_a, half_win_ms=150.0)
    peaks_e = rr_outlier_filter(peaks_e, fs, tol=0.25)
    summarise_bpm(peaks_e, fs, "E  template xcorr")

    # ── Method F: Welch PSD spectral ─────────────────────────────────────────
    print("\n[8] Method F — Welch PSD spectral HR...")
    bpm_f = spectral_hr(ecg_hp, fs)
    if not np.isnan(bpm_f):
        print(f"  F  spectral: {bpm_f:.1f} BPM")
    else:
        print(f"  F  spectral: estimation failed")

    # ── Method G: CWT wavelet peak detection ──────────────────────────────────
    print("\n[9] Method G — CWT wavelet peak detection...")
    peaks_g = detect_peaks_wavelet(ecg_hp, fs)
    peaks_g = rr_outlier_filter(peaks_g, fs, tol=0.25)
    summarise_bpm(peaks_g, fs, "G  CWT wavelet")

    # ── Method H: Hilbert envelope peak detection ─────────────────────────────
    print("\n[10] Method H — Hilbert envelope peak detection...")
    peaks_h = detect_peaks_hilbert(ecg_hp, fs)
    peaks_h = rr_outlier_filter(peaks_h, fs, tol=0.25)
    summarise_bpm(peaks_h, fs, "H  Hilbert env")

    print(f"\n  Expected HR during recording: ~70–80 BPM")

    # ── Figure ────────────────────────────────────────────────────────────────
    print("\n[11] Generating figure...")
    fig = make_figure(
        t=t,
        ecg_hp=ecg_hp,
        pt_env=pt_env,
        eog_hp=eog_hp,
        motion_metric=motion_metric,
        t_motion=t_motion,
        motion_threshold=motion_threshold,
        motion_unit=motion_unit,
        blink_mask=blink_mask,
        motion_mask=motion_mask,
        art_mask=art_mask,
        peaks_a=peaks_a,
        peaks_b=peaks_b,
        peaks_c=peaks_c,
        peaks_d=peaks_d,
        peaks_e=peaks_e,
        peaks_g=peaks_g,
        peaks_h=peaks_h,
        bpm_d=bpm_d,
        bpm_f=bpm_f,
        lag_d=lag_d,
        ac=ac,
        fs=fs,
    )

    here    = os.path.dirname(os.path.abspath(__file__))
    out_dir = args.outdir or os.path.join(here, "..", "figures")
    os.makedirs(out_dir, exist_ok=True)
    stem    = os.path.splitext(os.path.basename(args.edf))[0]
    outpath = os.path.join(out_dir, f"{stem}_hr_analysis.png")
    fig.savefig(outpath, dpi=150, bbox_inches="tight")
    print(f"  Saved -> {outpath}")

    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
