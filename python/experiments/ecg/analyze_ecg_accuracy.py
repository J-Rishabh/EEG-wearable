from __future__ import annotations

"""
analyze_ecg_accuracy.py — ECG benchmark: ADS1299 (EEG wearable) vs AD8232 (Arduino)
=====================================================================================
Loads a paired EEG recording (EDF + meta.json from eeg_stream_pg.py) and an Arduino
ECG recording (CSV + meta.json from record_arduino_ecg.py), aligns them in time using
rec_start_epoch from each meta JSON, then produces four thesis-quality figures:

  Fig 1  — Raw & filtered ECG waveforms, both devices, shared time axis
  Fig 2  — Instantaneous heart rate over the full recording
  Fig 3  — Bland-Altman + scatter: ADS1299 BPM vs AD8232 BPM
  Fig 4  — R-R interval comparison (Poincaré plot + time series)

Figures are saved to the figures/ folder next to this script (or --outdir).

Usage:
    # Specify both meta files explicitly:
    python analyze_ecg_accuracy.py \\
        --eeg     python/recordings/eeg_20240101_120000_meta.json \\
        --arduino python/recordings/arduino_ecg_20240101_120000_meta.json

    # Auto-pair: finds the arduino recording closest in time to the EEG recording:
    python analyze_ecg_accuracy.py --eeg python/recordings/eeg_20240101_120000_meta.json

    # Or auto-pair the other direction:
    python analyze_ecg_accuracy.py --arduino python/recordings/arduino_ecg_20240101_120000_meta.json

Requirements:
    pip install numpy scipy matplotlib pyedflib
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import numpy as np
from scipy.interpolate import interp1d
from scipy.signal import butter, filtfilt, find_peaks


# ── Styling (dark, matches the rest of the project) ──────────────────────────

DARK_BG   = "#0d1117"
PANEL_BG  = "#161b22"
BORDER    = "#30363d"
TEXT      = "#e6edf3"
MUTED     = "#8b949e"

C_ADS  = "#58a6ff"   # ADS1299 ECG — blue
C_ARD  = "#3fb950"   # AD8232 ECG  — green
C_PPG  = "#d2a8ff"   # AD8232 PPG  — purple
C_WARN = "#f85149"   # out-of-range / annotations

plt.rcParams.update({
    "figure.facecolor":  DARK_BG,
    "axes.facecolor":    PANEL_BG,
    "axes.edgecolor":    BORDER,
    "axes.labelcolor":   MUTED,
    "axes.titlecolor":   TEXT,
    "xtick.color":       MUTED,
    "ytick.color":       MUTED,
    "text.color":        TEXT,
    "grid.color":        "#21262d",
    "grid.linewidth":    0.6,
    "legend.facecolor":  PANEL_BG,
    "legend.edgecolor":  BORDER,
    "legend.labelcolor": TEXT,
    "font.size":         9,
})


# ── Signal processing helpers ─────────────────────────────────────────────────

def bandpass(data: np.ndarray, fs: float, lo: float = 0.5, hi: float = 40.0) -> np.ndarray:
    nyq = fs / 2.0
    b, a = butter(4, [lo / nyq, hi / nyq], btype="band")
    return filtfilt(b, a, data)


def notch(data: np.ndarray, fs: float, f0: float = 60.0, q: float = 30.0) -> np.ndarray:
    from scipy.signal import iirnotch
    b, a = iirnotch(f0 / (fs / 2.0), q)
    return filtfilt(b, a, data)


def detect_rpeaks(signal: np.ndarray, fs: float) -> np.ndarray:
    """Return sample indices of R-peaks in a filtered ECG."""
    min_dist = int(fs * 0.33)   # ~180 BPM max
    rng      = np.percentile(signal, 95) - np.percentile(signal, 5)
    height   = np.median(signal) + 0.4 * rng
    peaks, _ = find_peaks(signal, height=height, distance=min_dist)
    return peaks


def rpeaks_to_bpm_series(peaks: np.ndarray, fs: float, t_out: np.ndarray) -> np.ndarray:
    """
    Convert R-peak sample indices → instantaneous BPM time series on t_out grid.
    Uses cubic interpolation of 60/RR; returns NaN where extrapolation would be needed.
    """
    if len(peaks) < 3:
        return np.full(len(t_out), np.nan)

    rr_s   = np.diff(peaks) / fs                 # RR intervals in seconds
    bpm    = 60.0 / rr_s                          # instantaneous BPM per interval
    t_bpm  = (peaks[:-1] + peaks[1:]) / (2 * fs)  # midpoint time of each interval

    # Clamp physiologically implausible values (< 30 or > 220 BPM) to NaN
    bpm = np.where((bpm > 30) & (bpm < 220), bpm, np.nan)
    valid = ~np.isnan(bpm)
    if valid.sum() < 3:
        return np.full(len(t_out), np.nan)

    interp = interp1d(t_bpm[valid], bpm[valid], kind="cubic",
                      bounds_error=False, fill_value=np.nan)
    return interp(t_out)


def ppg_bpm_series(ppg: np.ndarray, fs: float, t_out: np.ndarray) -> np.ndarray:
    """Detect peaks in raw PulseSensor PPG and return BPM series."""
    ppg_std = np.std(ppg)
    if ppg_std < 8.0:
        return np.full(len(t_out), np.nan)   # no contact

    min_dist  = int(fs * 0.33)
    threshold = np.percentile(ppg, 65)
    peaks, _  = find_peaks(ppg, height=threshold, prominence=ppg_std * 0.5,
                            distance=min_dist)
    return rpeaks_to_bpm_series(peaks, fs, t_out)


def zscore(x: np.ndarray) -> np.ndarray:
    s = np.std(x)
    return (x - np.mean(x)) / s if s > 0 else x - np.mean(x)


# ── Data loaders ─────────────────────────────────────────────────────────────

def load_eeg_meta(meta_path: str) -> dict:
    with open(meta_path) as f:
        return json.load(f)


def load_eeg_signal(meta: dict, meta_dir: str) -> tuple[np.ndarray, float, float]:
    """
    Returns (ecg_uv, fs, rec_start_epoch).
    ECG = channel index 1 ('EMG_far') — the ADS1299 channel wired to the ECG electrode.
    """
    fs    = float(meta["fs"])
    t0    = float(meta["rec_start_epoch"])
    fname = meta.get("edf_file", "")

    edf_path = os.path.join(meta_dir, fname)
    if fname.endswith(".npy"):
        arr = np.load(edf_path)          # shape (N, 8)
        return arr[:, 1].astype(float), fs, t0

    try:
        import pyedflib
        f   = pyedflib.EdfReader(edf_path)
        ch2 = f.readSignal(1).astype(float)   # index 1 = EMG_far = ECG
        f._close()
        return ch2, fs, t0
    except ImportError:
        sys.exit("[ERROR] pyedflib not installed. Run: pip install pyedflib")
    except Exception as e:
        sys.exit(f"[ERROR] Could not read EDF {edf_path}: {e}")


def load_arduino_data(meta: dict, meta_dir: str) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Returns (ecg_adc, pulse_adc, fs, rec_start_epoch)."""
    import csv as csv_mod
    fs    = float(meta["fs"])
    t0    = float(meta["rec_start_epoch"])
    fname = meta.get("csv_file", "")
    path  = os.path.join(meta_dir, fname)

    ecg_list, ppg_list = [], []
    with open(path, newline="") as cf:
        reader = csv_mod.DictReader(cf)
        for row in reader:
            ecg_list.append(float(row["ecg_adc"]))
            ppg_list.append(float(row["pulse_adc"]))

    return np.array(ecg_list), np.array(ppg_list), fs, t0


# ── File auto-pairing ─────────────────────────────────────────────────────────

def find_closest_meta(ref_epoch: float, search_dir: str, prefix: str) -> str | None:
    """Find the meta JSON in search_dir whose rec_start_epoch is closest to ref_epoch."""
    candidates = list(Path(search_dir).glob(f"{prefix}*_meta.json"))
    if not candidates:
        return None
    best, best_dt = None, float("inf")
    for p in candidates:
        try:
            m  = json.loads(p.read_text())
            dt = abs(m["rec_start_epoch"] - ref_epoch)
            if dt < best_dt:
                best_dt, best = dt, str(p)
        except Exception:
            continue
    return best


# ── Statistics helpers ────────────────────────────────────────────────────────

def bland_altman_stats(a: np.ndarray, b: np.ndarray) -> dict:
    diff = a - b
    mean = (a + b) / 2.0
    bias = np.nanmean(diff)
    sd   = np.nanstd(diff)
    return {
        "bias":    bias,
        "loa_hi":  bias + 1.96 * sd,
        "loa_lo":  bias - 1.96 * sd,
        "sd":      sd,
        "mean":    mean,
        "diff":    diff,
    }


def pearson_r(a: np.ndarray, b: np.ndarray) -> float:
    mask = ~(np.isnan(a) | np.isnan(b))
    if mask.sum() < 3:
        return np.nan
    return float(np.corrcoef(a[mask], b[mask])[0, 1])


def rmse(a: np.ndarray, b: np.ndarray) -> float:
    mask = ~(np.isnan(a) | np.isnan(b))
    return float(np.sqrt(np.nanmean((a[mask] - b[mask]) ** 2)))


def mae(a: np.ndarray, b: np.ndarray) -> float:
    mask = ~(np.isnan(a) | np.isnan(b))
    return float(np.nanmean(np.abs(a[mask] - b[mask])))


# ── Plotting ─────────────────────────────────────────────────────────────────

def _ax_style(ax, title="", xlabel="", ylabel="", grid=True):
    ax.set_title(title, fontsize=10, fontweight="bold", pad=6)
    ax.set_xlabel(xlabel, fontsize=8)
    ax.set_ylabel(ylabel, fontsize=8)
    for sp in ax.spines.values():
        sp.set_edgecolor(BORDER)
    if grid:
        ax.grid(True)


def plot_waveforms(
    t_ads: np.ndarray, ecg_ads_filt: np.ndarray,
    t_ard: np.ndarray, ecg_ard_filt: np.ndarray,
    overlap_start: float, preview_sec: float = 10.0,
    outpath: str | None = None,
):
    """Fig 1 — side-by-side filtered ECG waveforms for the first preview_sec of overlap."""
    fig, axes = plt.subplots(2, 1, figsize=(13, 6), sharex=True,
                              gridspec_kw={"hspace": 0.12})
    fig.suptitle("ECG Waveform Comparison: ADS1299 vs AD8232",
                 fontsize=13, fontweight="bold", y=0.97)

    t_end = overlap_start + preview_sec

    # ADS1299
    mask_a = (t_ads >= overlap_start) & (t_ads < t_end)
    ta = t_ads[mask_a] - overlap_start
    ya = zscore(ecg_ads_filt[mask_a])
    axes[0].plot(ta, ya, color=C_ADS, lw=0.9, label="ADS1299 CH2 (filtered, z-scored)")
    _ax_style(axes[0], title="ADS1299 CH2 — EEG Wearable ECG", ylabel="Amplitude (z-score)")
    axes[0].legend(loc="upper right", fontsize=8)

    # AD8232
    mask_b = (t_ard >= overlap_start) & (t_ard < t_end)
    tb = t_ard[mask_b] - overlap_start
    yb = zscore(ecg_ard_filt[mask_b])
    axes[1].plot(tb, yb, color=C_ARD, lw=0.9, label="AD8232 (filtered, z-scored)")
    _ax_style(axes[1], title="AD8232 — Arduino ECG",
              xlabel=f"Time within overlap (s)", ylabel="Amplitude (z-score)")
    axes[1].legend(loc="upper right", fontsize=8)

    fig.tight_layout()
    if outpath:
        fig.savefig(outpath, dpi=150, bbox_inches="tight")
        print(f"[FIG 1] Saved -> {outpath}")
    return fig


def plot_heart_rate(
    t_grid: np.ndarray,
    bpm_ads: np.ndarray,
    bpm_ard: np.ndarray,
    bpm_ppg: np.ndarray,
    outpath: str | None = None,
):
    """Fig 2 — instantaneous heart rate over time for all three sources."""
    fig, ax = plt.subplots(figsize=(13, 4.5))
    fig.suptitle("Instantaneous Heart Rate Comparison", fontsize=13, fontweight="bold", y=0.97)

    ax.plot(t_grid, bpm_ads, color=C_ADS,  lw=1.3, label="ADS1299 ECG (EEG wearable)")
    ax.plot(t_grid, bpm_ard, color=C_ARD,  lw=1.3, label="AD8232 ECG (Arduino)")
    ax.plot(t_grid, bpm_ppg, color=C_PPG,  lw=1.0, alpha=0.7, label="AD8232 PPG (PulseSensor)")

    _ax_style(ax, xlabel="Time in overlap (s)", ylabel="Heart Rate (BPM)")
    ax.legend(loc="upper right", fontsize=8)
    ax.set_ylim(40, 130)

    fig.tight_layout()
    if outpath:
        fig.savefig(outpath, dpi=150, bbox_inches="tight")
        print(f"[FIG 2] Saved -> {outpath}")
    return fig


def plot_bland_altman_scatter(
    bpm_ads: np.ndarray,
    bpm_ard: np.ndarray,
    outpath: str | None = None,
):
    """Fig 3 — Bland-Altman agreement + scatter with identity line."""
    mask = ~(np.isnan(bpm_ads) | np.isnan(bpm_ard))
    a, b = bpm_ads[mask], bpm_ard[mask]
    if len(a) < 5:
        print("[WARN] Not enough paired BPM points for Bland-Altman plot.")
        return None

    ba   = bland_altman_stats(a, b)
    r    = pearson_r(a, b)
    mae_ = mae(a, b)
    rmse_= rmse(a, b)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle("ADS1299 ECG vs AD8232 ECG — Agreement Analysis",
                 fontsize=13, fontweight="bold", y=0.97)

    # ── Scatter ──
    ax = axes[0]
    lim_lo = min(a.min(), b.min()) - 3
    lim_hi = max(a.max(), b.max()) + 3
    ax.scatter(a, b, color=C_ADS, alpha=0.5, s=18, label=f"n={len(a)}")
    ax.plot([lim_lo, lim_hi], [lim_lo, lim_hi], color=MUTED, lw=1.2, ls="--", label="Identity")
    ax.set_xlim(lim_lo, lim_hi)
    ax.set_ylim(lim_lo, lim_hi)
    ax.set_aspect("equal", adjustable="box")
    _ax_style(ax, title="Scatter: ADS1299 vs AD8232",
              xlabel="ADS1299 Heart Rate (BPM)", ylabel="AD8232 Heart Rate (BPM)")
    ax.annotate(f"r = {r:.3f}\nMAE = {mae_:.1f} BPM\nRMSE = {rmse_:.1f} BPM",
                xy=(0.04, 0.94), xycoords="axes fraction",
                va="top", fontsize=8.5,
                bbox=dict(boxstyle="round,pad=0.4", facecolor=PANEL_BG, edgecolor=BORDER))
    ax.legend(loc="lower right", fontsize=8)

    # ── Bland-Altman ──
    ax2 = axes[1]
    ax2.scatter(ba["mean"], ba["diff"], color=C_ARD, alpha=0.5, s=18)
    ax2.axhline(ba["bias"],   color=C_ADS,  lw=1.5, ls="-",  label=f"Bias = {ba['bias']:+.1f}")
    ax2.axhline(ba["loa_hi"], color=C_WARN, lw=1.2, ls="--", label=f"+1.96 SD = {ba['loa_hi']:+.1f}")
    ax2.axhline(ba["loa_lo"], color=C_WARN, lw=1.2, ls="--", label=f"−1.96 SD = {ba['loa_lo']:+.1f}")
    ax2.axhline(0, color=MUTED, lw=0.8, ls=":")
    _ax_style(ax2, title="Bland-Altman: ADS1299 − AD8232",
              xlabel="Mean BPM (ADS1299 + AD8232) / 2",
              ylabel="Difference BPM (ADS1299 − AD8232)")
    ax2.legend(loc="upper right", fontsize=8)

    fig.tight_layout()
    if outpath:
        fig.savefig(outpath, dpi=150, bbox_inches="tight")
        print(f"[FIG 3] Saved -> {outpath}")
    return fig


def plot_rr_comparison(
    peaks_ads: np.ndarray, fs_ads: float,
    peaks_ard: np.ndarray, fs_ard: float,
    outpath: str | None = None,
):
    """Fig 4 — R-R interval Poincaré plot + R-R time series for both devices."""
    if len(peaks_ads) < 3 or len(peaks_ard) < 3:
        print("[WARN] Insufficient R-peaks for R-R comparison plot.")
        return None

    rr_ads = np.diff(peaks_ads) / fs_ads * 1000   # ms
    rr_ard = np.diff(peaks_ard) / fs_ard * 1000

    # Times of each RR interval (midpoint of the two peaks, relative to start)
    t_rr_ads = (peaks_ads[:-1] + peaks_ads[1:]) / (2 * fs_ads)
    t_rr_ard = (peaks_ard[:-1] + peaks_ard[1:]) / (2 * fs_ard)

    fig = plt.figure(figsize=(13, 6))
    fig.suptitle("R-R Interval Comparison: ADS1299 vs AD8232",
                 fontsize=13, fontweight="bold", y=0.97)
    gs = gridspec.GridSpec(1, 2, wspace=0.30)

    # Poincaré
    ax_p = fig.add_subplot(gs[0, 0])
    ax_p.scatter(rr_ads[:-1], rr_ads[1:], color=C_ADS, alpha=0.5, s=22, label="ADS1299")
    ax_p.scatter(rr_ard[:-1], rr_ard[1:], color=C_ARD, alpha=0.5, s=22, label="AD8232")
    _ax_style(ax_p, title="Poincaré Plot (RR[n] vs RR[n+1])",
              xlabel="RR[n] (ms)", ylabel="RR[n+1] (ms)")
    ax_p.legend(fontsize=8)

    # Time series
    ax_t = fig.add_subplot(gs[0, 1])
    ax_t.plot(t_rr_ads, rr_ads, color=C_ADS, lw=1.0, marker=".", markersize=3, label="ADS1299")
    ax_t.plot(t_rr_ard, rr_ard, color=C_ARD, lw=1.0, marker=".", markersize=3, label="AD8232")
    _ax_style(ax_t, title="R-R Interval Time Series",
              xlabel="Time (s)", ylabel="R-R interval (ms)")
    ax_t.legend(fontsize=8)

    fig.tight_layout()
    if outpath:
        fig.savefig(outpath, dpi=150, bbox_inches="tight")
        print(f"[FIG 4] Saved -> {outpath}")
    return fig


# ── Main pipeline ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Benchmark EEG-wearable ECG against Arduino AD8232 reference"
    )
    parser.add_argument("--eeg",
                        help="Path to eeg_*_meta.json from eeg_stream_pg.py")
    parser.add_argument("--arduino",
                        help="Path to arduino_ecg_*_meta.json from record_arduino_ecg.py")
    parser.add_argument("--outdir", default=None,
                        help="Directory for saved figures (default: figures/ next to this script)")
    parser.add_argument("--preview", type=float, default=10.0,
                        help="Seconds of overlap to show in waveform plot (default: 10)")
    parser.add_argument("--show", action="store_true",
                        help="Show interactive figures instead of (or in addition to) saving")
    args = parser.parse_args()

    if not args.eeg and not args.arduino:
        parser.error("Provide at least one of --eeg or --arduino.")

    # ── Locate meta files ────────────────────────────────────────────────────
    here     = os.path.dirname(os.path.abspath(__file__))
    rec_dir  = os.path.normpath(os.path.join(here, "..", "..", "recordings"))

    eeg_meta_path = args.eeg
    ard_meta_path = args.arduino

    if eeg_meta_path and not ard_meta_path:
        m0    = load_eeg_meta(eeg_meta_path)
        found = find_closest_meta(m0["rec_start_epoch"], rec_dir, "arduino_ecg_")
        if not found:
            sys.exit(f"[ERROR] No arduino_ecg_*_meta.json found in {rec_dir}. "
                     "Pass --arduino explicitly.")
        ard_meta_path = found
        print(f"[AUTO] Paired Arduino recording: {ard_meta_path}")

    elif ard_meta_path and not eeg_meta_path:
        m0    = load_eeg_meta(ard_meta_path)
        found = find_closest_meta(m0["rec_start_epoch"], rec_dir, "eeg_")
        if not found:
            sys.exit(f"[ERROR] No eeg_*_meta.json found in {rec_dir}. Pass --eeg explicitly.")
        eeg_meta_path = found
        print(f"[AUTO] Paired EEG recording: {eeg_meta_path}")

    # ── Load data ────────────────────────────────────────────────────────────
    print("\nLoading EEG recording...")
    eeg_meta  = load_eeg_meta(eeg_meta_path)
    eeg_dir   = os.path.dirname(os.path.abspath(eeg_meta_path))
    ecg_ads, fs_ads, t0_ads = load_eeg_signal(eeg_meta, eeg_dir)
    print(f"  ADS1299: {len(ecg_ads)} samples @ {fs_ads:.0f} Hz  "
          f"({len(ecg_ads)/fs_ads:.1f} s)  start={eeg_meta['rec_start_iso']}")

    print("Loading Arduino recording...")
    ard_meta  = load_eeg_meta(ard_meta_path)
    ard_dir   = os.path.dirname(os.path.abspath(ard_meta_path))
    ecg_ard, ppg_ard, fs_ard, t0_ard = load_arduino_data(ard_meta, ard_dir)
    print(f"  AD8232:  {len(ecg_ard)} samples @ {fs_ard:.0f} Hz  "
          f"({len(ecg_ard)/fs_ard:.1f} s)  start={ard_meta['rec_start_iso']}")

    # ── Absolute time axes ───────────────────────────────────────────────────
    t_ads_abs = t0_ads + np.arange(len(ecg_ads)) / fs_ads
    t_ard_abs = t0_ard + np.arange(len(ecg_ard)) / fs_ard

    overlap_start = max(t0_ads, t0_ard)
    overlap_end   = min(t_ads_abs[-1], t_ard_abs[-1])

    if overlap_start >= overlap_end:
        sys.exit("[ERROR] The two recordings have no temporal overlap. "
                 "Check that they were recorded simultaneously.")

    overlap_dur = overlap_end - overlap_start
    print(f"\nOverlap window: {overlap_dur:.1f} s  "
          f"({datetime.fromtimestamp(overlap_start).isoformat()} → "
          f"{datetime.fromtimestamp(overlap_end).isoformat()})")
    print(f"  Time offset between starts: "
          f"{t0_ard - t0_ads:+.2f} s (Arduino − EEG)")

    # ── Clip both signals to the overlap window ───────────────────────────────
    mask_ads = (t_ads_abs >= overlap_start) & (t_ads_abs < overlap_end)
    mask_ard = (t_ard_abs >= overlap_start) & (t_ard_abs < overlap_end)

    ecg_ads_clip = ecg_ads[mask_ads]
    ecg_ard_clip = ecg_ard[mask_ard]
    ppg_ard_clip = ppg_ard[mask_ard]

    t_ads_rel = t_ads_abs[mask_ads] - overlap_start   # relative time within overlap
    t_ard_rel = t_ard_abs[mask_ard] - overlap_start

    # ── Filter ───────────────────────────────────────────────────────────────
    print("Filtering signals...")
    ecg_ads_filt = bandpass(ecg_ads_clip, fs_ads)
    ecg_ard_filt = bandpass(ecg_ard_clip, fs_ard)
    # 60 Hz notch on Arduino (not pre-filtered like ADS1299)
    ecg_ard_filt = notch(ecg_ard_filt, fs_ard)

    # ── Detect R-peaks ───────────────────────────────────────────────────────
    print("Detecting R-peaks...")
    peaks_ads = detect_rpeaks(ecg_ads_filt, fs_ads)
    peaks_ard = detect_rpeaks(ecg_ard_filt, fs_ard)
    print(f"  ADS1299: {len(peaks_ads)} R-peaks detected")
    print(f"  AD8232:  {len(peaks_ard)} R-peaks detected")

    # ── Build BPM time series on a shared 1 Hz grid ──────────────────────────
    t_grid = np.arange(0, overlap_dur, 1.0)    # 1 Hz common grid
    bpm_ads = rpeaks_to_bpm_series(peaks_ads, fs_ads, t_grid)
    bpm_ard = rpeaks_to_bpm_series(peaks_ard, fs_ard, t_grid)
    bpm_ppg = ppg_bpm_series(ppg_ard_clip, fs_ard, t_grid)

    # ── Summary statistics ────────────────────────────────────────────────────
    print("\n── Heart Rate Statistics ──────────────────────────────────────────")
    for label, bpm in [("ADS1299 ECG", bpm_ads), ("AD8232  ECG", bpm_ard), ("AD8232  PPG", bpm_ppg)]:
        valid = bpm[~np.isnan(bpm)]
        if len(valid):
            print(f"  {label}: mean={np.mean(valid):.1f}  "
                  f"std={np.std(valid):.1f}  "
                  f"range=[{np.min(valid):.0f}, {np.max(valid):.0f}] BPM")
        else:
            print(f"  {label}: no valid BPM data")

    print("\n── Accuracy vs AD8232 ECG (reference) ────────────────────────────")
    mask_pair = ~(np.isnan(bpm_ads) | np.isnan(bpm_ard))
    if mask_pair.sum() >= 5:
        a, b = bpm_ads[mask_pair], bpm_ard[mask_pair]
        ba   = bland_altman_stats(a, b)
        print(f"  Paired samples : {mask_pair.sum()}")
        print(f"  Pearson r      : {pearson_r(a, b):.4f}")
        print(f"  MAE            : {mae(a, b):.2f} BPM")
        print(f"  RMSE           : {rmse(a, b):.2f} BPM")
        print(f"  Bias (mean diff): {ba['bias']:+.2f} BPM")
        print(f"  LoA            : [{ba['loa_lo']:+.2f}, {ba['loa_hi']:+.2f}] BPM")
    else:
        print("  Not enough paired points for statistics.")

    # ── Output directory for figures ─────────────────────────────────────────
    if args.outdir:
        out_dir = args.outdir
    else:
        out_dir = os.path.join(here, "..", "figures")
    os.makedirs(out_dir, exist_ok=True)

    # Timestamp prefix for figure filenames
    ts = datetime.fromtimestamp(overlap_start).strftime("%Y%m%d_%H%M%S")

    # ── Generate figures ─────────────────────────────────────────────────────
    print("\nGenerating figures...")

    plot_waveforms(
        t_ads_rel, ecg_ads_filt,
        t_ard_rel, ecg_ard_filt,
        overlap_start=0.0, preview_sec=min(args.preview, overlap_dur),
        outpath=os.path.join(out_dir, f"ecg_waveforms_{ts}.png"),
    )

    plot_heart_rate(
        t_grid, bpm_ads, bpm_ard, bpm_ppg,
        outpath=os.path.join(out_dir, f"ecg_heartrate_{ts}.png"),
    )

    plot_bland_altman_scatter(
        bpm_ads, bpm_ard,
        outpath=os.path.join(out_dir, f"ecg_bland_altman_{ts}.png"),
    )

    plot_rr_comparison(
        peaks_ads, fs_ads,
        peaks_ard, fs_ard,
        outpath=os.path.join(out_dir, f"ecg_rr_comparison_{ts}.png"),
    )

    print(f"\nAll figures saved to: {os.path.abspath(out_dir)}")

    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
