"""
hr_from_edf.py — HR estimation from a single ADS1299 EDF recording
====================================================================
Tries three progressively more robust approaches on the ECG channel (CH2 = EMG_far):

  Method A  — simple peak detection on HP-filtered signal (what the live viewer does now)
  Method B  — Pan-Tompkins pipeline (bandpass → derivative → square → moving-average)
  Method C  — Pan-Tompkins + artifact gating using EOG (blink) and multi-channel
               jump detection (motion)

Prints BPM estimate from each method and saves a figure showing the waveform,
detected R-peaks, artifact windows, and a per-method summary panel.

Usage:
    python hr_from_edf.py recordings/eeg_20260408_204723.edf
    python hr_from_edf.py recordings/eeg_20260408_204723.edf --show
"""

import argparse
import os
import sys

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from scipy.signal import butter, filtfilt, iirnotch, find_peaks

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


def rr_to_bpm(peaks: np.ndarray, fs: float) -> tuple[np.ndarray, np.ndarray]:
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


def detect_motion_mask(all_ch: np.ndarray, fs: float,
                       jump_uv: float = 5000.0, pad_ms: float = 200.0) -> np.ndarray:
    """
    Boolean mask: True where a motion artifact is present.
    Motion artifacts cause simultaneous large jumps across multiple channels.
    Detected as: the max absolute sample-to-sample delta across all 8 channels
    exceeds jump_uv at the same sample.
    """
    diffs  = np.abs(np.diff(all_ch, axis=0))     # (N-1, 8)
    metric = diffs.max(axis=1)                    # (N-1,) — worst channel at each step
    mask   = np.concatenate([[False], metric > jump_uv])
    pad    = int(pad_ms * fs / 1000)
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
    blink_mask: np.ndarray,
    motion_mask: np.ndarray,
    art_mask: np.ndarray,
    peaks_a: np.ndarray,
    peaks_b: np.ndarray,
    peaks_c: np.ndarray,
    fs: float,
) -> plt.Figure:
    fig, axes = plt.subplots(
        5, 1, figsize=(13, 14),
        gridspec_kw={"height_ratios": [3, 1.5, 1.5, 1.2, 2.5], "hspace": 0.45},
    )
    fig.suptitle(
        "ECG HR Estimation — ADS1299 EDF  (CH2 = EMG_far / ECG electrode)",
        fontsize=12, fontweight="bold", y=0.98,
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

    shade_artifacts(ax, art_mask, t)
    _ax(ax, title="ECG waveform + detected R-peaks (all 3 methods)",
        xlabel="Time (s)", ylabel="Amplitude (z-score)")
    ax.legend(loc="upper right", fontsize=8, ncol=2)

    # ── Panel 1: Pan-Tompkins envelope ───────────────────────────────────────
    ax = axes[1]
    ax.plot(t, pt_env / pt_env.max(), color=C_PT, lw=0.9, label="PT envelope (normalised)")
    if len(peaks_b):
        ax.scatter(t[peaks_b], pt_env[peaks_b] / pt_env.max(), color=C_PEAK, s=50, zorder=5, marker="^")
    shade_artifacts(ax, art_mask, t)
    _ax(ax, title="Pan-Tompkins decision signal", xlabel="Time (s)", ylabel="Normalised")
    ax.legend(fontsize=8)

    # ── Panel 2: EOG (blink detection) ───────────────────────────────────────
    ax = axes[2]
    ax.plot(t, eog_hp, color=C_EOG, lw=0.8, label="EOG (HP-filtered)")
    ax.fill_between(t, 0, blink_mask.astype(float) * eog_hp.max(),
                    where=blink_mask, color=C_ART, alpha=0.25, label="blink mask")
    _ax(ax, title="EOG channel — blink artifact detection", xlabel="Time (s)", ylabel="µV")
    ax.legend(fontsize=8)

    # ── Panel 3: Motion metric ────────────────────────────────────────────────
    ax = axes[3]
    t_motion = t[1:]   # diff has N-1 samples
    ax.plot(t_motion, motion_metric, color=C_MOTION, lw=0.8, label="max |Δ| across channels")
    ax.axhline(5000, color=C_ART, lw=1.0, ls="--", label="threshold (5000 µV/sample)")
    shade_artifacts(ax, motion_mask, t)
    _ax(ax, title="Motion artifact metric (max sample-to-sample jump across all 8 channels)",
        xlabel="Time (s)", ylabel="µV/sample")
    ax.legend(fontsize=8)
    ax.set_yscale("log")

    # ── Panel 4: BPM per-interval bar chart ──────────────────────────────────
    ax = axes[4]
    bar_w = 0.25
    methods = [
        ("A  simple",        peaks_a, "#ffa726"),
        ("B  Pan-Tompkins",  peaks_b, C_PEAK),
        ("C  PT + gating",   peaks_c, "#d2a8ff"),
    ]
    for mi, (label, peaks, color) in enumerate(methods):
        if len(peaks) < 2:
            continue
        _, bpm = rr_to_bpm(peaks, fs)
        # Each interval plotted at midpoint time between its two peaks
        t_mid = (t[peaks[:-1]] + t[peaks[1:]]) / 2
        x_pos = t_mid + (mi - 1) * bar_w * 0.4
        valid = ~np.isnan(bpm)
        ax.bar(x_pos[valid], bpm[valid], width=bar_w * 0.35, color=color,
               alpha=0.75, label=label, zorder=3)
        if valid.any():
            mean_bpm = np.mean(bpm[valid])
            ax.axhline(mean_bpm, color=color, lw=1.2, ls="--", alpha=0.7)

    # Reference range
    ax.axhspan(70, 80, color="#ffffff", alpha=0.05, label="expected 70–80 BPM")
    ax.set_ylim(30, 140)
    _ax(ax, title="Instantaneous BPM per RR interval (dashed = mean)",
        xlabel="Time (s)", ylabel="BPM")
    ax.legend(fontsize=8, loc="upper right")

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
    labels = f.getSignalLabels()
    n      = f.getNSamples()[0]
    fs     = float(f.getSampleFrequency(0))
    sigs   = np.array([f.readSignal(i) for i in range(f.signals_in_file)])  # (8, N)
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
    motion_mask = detect_motion_mask(sigs.T, fs, jump_uv=5000.0, pad_ms=200.0)
    art_mask    = blink_mask | motion_mask

    n_blink  = int(blink_mask.sum())
    n_motion = int(motion_mask.sum())
    n_art    = int(art_mask.sum())
    print(f"  Blink  mask: {n_blink} samples ({100*n_blink/n:.1f}%)")
    print(f"  Motion mask: {n_motion} samples ({100*n_motion/n:.1f}%)")
    print(f"  Combined:    {n_art} samples ({100*n_art/n:.1f}%) gated out")

    # For the motion metric plot
    motion_metric = np.abs(np.diff(sigs, axis=1)).max(axis=0)   # (N-1,)

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

    print(f"\n  Expected HR during recording: ~70–80 BPM")

    # ── Figure ────────────────────────────────────────────────────────────────
    print("\n[6] Generating figure...")
    fig = make_figure(
        t=t,
        ecg_hp=ecg_hp,
        pt_env=pt_env,
        eog_hp=eog_hp,
        motion_metric=motion_metric,
        blink_mask=blink_mask,
        motion_mask=motion_mask,
        art_mask=art_mask,
        peaks_a=peaks_a,
        peaks_b=peaks_b,
        peaks_c=peaks_c,
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
