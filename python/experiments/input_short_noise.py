#!/usr/bin/env python3
"""
input_short_noise.py — Input-referred noise characterisation for ADS1299 EEG wearable
=======================================================================================
Loads an EDF recorded with all inputs shorted to AGND (DRL off, SRB1 to AGND)
and characterises the noise floor across all active channels.

Typical usage:
    python input_short_noise.py ../recordings/eeg_XXXXXX_SHORT.edf
    python input_short_noise.py ../recordings/eeg_XXXXXX_SHORT.edf --trim 2 --save

Arguments:
    edf         Path to EDF file
    --trim      Seconds to discard at start and end (default: 2.0)
    --save      Save figures to experiments/figures/ instead of showing
"""

import sys
import os
import argparse
from typing import Optional, Tuple, List

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.patches import Patch
from scipy.signal import welch
from scipy.stats import norm

try:
    import pyedflib
except ImportError:
    print("[ERROR] pyedflib not found.  pip install pyEDFlib")
    sys.exit(1)

# ── Channel metadata ──────────────────────────────────────────────────────────

CH_LABELS = [
    "CH1 EOG",
    "CH2 EMG_far",
    "CH3 EMG_near",
    "CH4 EEG_L1",
    "CH5 EEG_L2",
    "CH6 EEG_L3",
    "CH7 SRB1_ref",
    "CH8 DRL",
]
CH_COLORS = [
    "#4fc3f7",
    "#ef5350",
    "#ffa726",
    "#66bb6a",
    "#ab47bc",
    "#26c6da",
    "#90a4ae",
    "#78909c",
]

# Channels to skip in analysis — CH7 (powered down) and CH8 (BIAS_MEAS)
SKIP_CH = {6, 7}   # 0-indexed

# EEG band for integrated noise
EEG_BAND_HZ  = (0.5, 40.0)
WIDE_BAND_HZ = (0.5, 100.0)

# ADS1299 datasheet input-referred noise at gain=24 — ~1 µV/√Hz typical
ADS_SPEC_UV_SQRTHZ = 1.0


# ── EDF loader ────────────────────────────────────────────────────────────────

def load_edf(path: str) -> Tuple[np.ndarray, int]:
    """Return (data_uv, fs) — data shape (N, min(n_signals, 8))."""
    f = pyedflib.EdfReader(path)
    try:
        fs     = int(f.getSampleFrequency(0))
        n_ch   = min(f.signals_in_file, 8)
        n_samp = f.getNSamples()[0]
        data   = np.zeros((n_samp, n_ch), dtype=np.float64)
        for i in range(n_ch):
            data[:, i] = f.readSignal(i)
    finally:
        f.close()
    return data, fs


# ── Noise helpers ─────────────────────────────────────────────────────────────

def integrated_rms(f_psd: np.ndarray, pxx: np.ndarray,
                   f_lo: float, f_hi: float) -> float:
    """Integrate PSD (µV²/Hz) over [f_lo, f_hi] Hz → RMS in µV."""
    mask = (f_psd >= f_lo) & (f_psd <= f_hi)
    if not np.any(mask):
        return float("nan")
    return float(np.sqrt(np.trapz(pxx[mask], f_psd[mask])))


# ── Main analysis ─────────────────────────────────────────────────────────────

def analyse(edf_path: str, trim_s: float = 2.0, save_figs: bool = False) -> None:

    # ── Load ──────────────────────────────────────────────────────────────────
    print(f"\n[LOAD]  {edf_path}")
    data, fs = load_edf(edf_path)
    n_total, n_ch = data.shape
    print(f"        {n_total} samples × {n_ch} ch  |  fs={fs} Hz  |  "
          f"{n_total/fs:.1f} s")

    trim_n = int(trim_s * fs)
    if 2 * trim_n >= n_total:
        print(f"[WARN]  Recording too short to trim {trim_s} s — skipping")
        trim_n = 0
    seg = data[trim_n : n_total - trim_n] if trim_n > 0 else data
    n_seg = len(seg)
    print(f"        Analysis window: {n_seg/fs:.1f} s  (trimmed {trim_s} s each end)\n")

    active = [ci for ci in range(n_ch) if ci not in SKIP_CH]

    # ── Compute PSD for each active channel ───────────────────────────────────
    nperseg  = min(4 * fs, n_seg)
    noverlap = nperseg // 2

    psds: List[Tuple[np.ndarray, np.ndarray]] = []   # (freqs, pxx_uv2_hz)
    rms_eeg:  List[float] = []
    rms_wide: List[float] = []

    print(f"  {'Channel':<18}  {'RMS EEG band (uV)':>18}  {'RMS wide band (uV)':>19}  "
          f"{'Peak NSD (uV/rtHz)':>18}")
    print(f"  {'-'*18}  {'-'*18}  {'-'*19}  {'-'*18}")

    for ci in active:
        sig = seg[:, ci] - np.mean(seg[:, ci])   # remove DC
        f_p, pxx = welch(sig, fs=fs, nperseg=nperseg,
                         noverlap=noverlap, window="hann")
        psds.append((f_p, pxx))

        r_eeg  = integrated_rms(f_p, pxx, *EEG_BAND_HZ)
        r_wide = integrated_rms(f_p, pxx, *WIDE_BAND_HZ)
        rms_eeg.append(r_eeg)
        rms_wide.append(r_wide)

        # Peak NSD in EEG band (µV/√Hz)
        eeg_mask   = (f_p >= EEG_BAND_HZ[0]) & (f_p <= EEG_BAND_HZ[1])
        peak_nsd   = float(np.sqrt(np.max(pxx[eeg_mask]))) if np.any(eeg_mask) else float("nan")

        print(f"  {CH_LABELS[ci]:<18}  {r_eeg:>18.3f}  {r_wide:>19.3f}  {peak_nsd:>18.4f}")

    print()

    plt.style.use("dark_background")
    fig_dir  = os.path.join(os.path.dirname(__file__), "figures")
    os.makedirs(fig_dir, exist_ok=True)
    edf_base = os.path.splitext(os.path.basename(edf_path))[0]

    # ── Figure 1: PSD per channel ─────────────────────────────────────────────
    fig1, ax1 = plt.subplots(figsize=(13, 6))
    fig1.patch.set_facecolor("#111111")
    ax1.set_facecolor("#111111")

    # Shade EEG band
    ax1.axvspan(*EEG_BAND_HZ, alpha=0.06, color="#66bb6a", label="EEG band (0.5–40 Hz)")

    for idx, ci in enumerate(active):
        f_p, pxx = psds[idx]
        nsd = np.sqrt(pxx)   # µV/√Hz
        ax1.semilogy(f_p, nsd,
                     color=CH_COLORS[ci], lw=1.4, alpha=0.85,
                     label=CH_LABELS[ci])

    # ADS1299 datasheet spec line
    ax1.axhline(ADS_SPEC_UV_SQRTHZ, color="#ffd54f", lw=1.0, ls="--",
                label=f"ADS1299 spec ~{ADS_SPEC_UV_SQRTHZ} µV/√Hz")

    ax1.set_xlabel("Frequency (Hz)", color="#aaaaaa")
    ax1.set_ylabel("Noise Spectral Density (µV/√Hz)", color="#aaaaaa")
    ax1.set_title(f"Input-Referred Noise PSD — all channels (inputs shorted)\n{edf_base}",
                  color="#dddddd")
    ax1.set_xlim(0, min(120, fs / 2))
    ax1.tick_params(colors="#888888")
    ax1.spines[:].set_color("#333333")
    ax1.legend(loc="upper right", fontsize=8,
               facecolor="#1a1a1a", edgecolor="#333333", labelcolor="#cccccc")
    ax1.grid(True, which="both", alpha=0.12, color="#444444")
    fig1.tight_layout()

    # ── Figure 2: Integrated RMS bar chart ────────────────────────────────────
    labels_b = [CH_LABELS[ci] for ci in active]
    colors_b = [CH_COLORS[ci] for ci in active]
    x        = np.arange(len(active))
    width    = 0.35

    fig2, ax2 = plt.subplots(figsize=(11, 5))
    fig2.patch.set_facecolor("#111111")
    ax2.set_facecolor("#111111")

    bars_eeg  = ax2.bar(x - width/2, rms_eeg,  width, label=f"EEG band ({EEG_BAND_HZ[0]}–{EEG_BAND_HZ[1]} Hz)",
                        color=[c + "cc" for c in colors_b], edgecolor="#2a2a2a")
    bars_wide = ax2.bar(x + width/2, rms_wide, width, label=f"Wide band ({WIDE_BAND_HZ[0]}–{WIDE_BAND_HZ[1]} Hz)",
                        color=[c + "66" for c in colors_b], edgecolor="#2a2a2a")

    # Annotate bar values
    for bar in list(bars_eeg) + list(bars_wide):
        h = bar.get_height()
        ax2.text(bar.get_x() + bar.get_width() / 2, h + 0.02,
                 f"{h:.2f}", ha="center", va="bottom",
                 color="#aaaaaa", fontsize=7)

    ax2.set_xticks(x)
    ax2.set_xticklabels(labels_b, rotation=20, ha="right", fontsize=8)
    ax2.set_ylabel("RMS Noise (µV)", color="#aaaaaa")
    ax2.set_title(f"Integrated RMS Noise — EEG & wide band\n{edf_base}",
                  color="#dddddd")
    ax2.tick_params(colors="#888888")
    ax2.spines[:].set_color("#333333")
    ax2.legend(loc="upper right", fontsize=8,
               facecolor="#1a1a1a", edgecolor="#333333", labelcolor="#cccccc")
    ax2.grid(True, axis="y", alpha=0.12, color="#444444")
    fig2.tight_layout()

    # ── Figure 3: Time-domain traces ──────────────────────────────────────────
    # Show 10 s starting from 10% into the trimmed segment
    t_win_s  = 10.0
    t_start  = max(0, n_seg // 10)
    t_end    = min(t_start + int(t_win_s * fs), n_seg)
    t_ax     = np.arange(t_end - t_start) / fs

    n_active = len(active)
    fig3, axes3 = plt.subplots(n_active, 1,
                               figsize=(13, 2.2 * n_active),
                               sharex=True)
    fig3.patch.set_facecolor("#111111")
    if n_active == 1:
        axes3 = [axes3]

    for ax, ci in zip(axes3, active):
        ax.set_facecolor("#111111")
        trace = seg[t_start:t_end, ci]
        trace = trace - np.mean(trace)
        ax.plot(t_ax, trace, color=CH_COLORS[ci], lw=0.8)

        pp   = float(np.max(trace) - np.min(trace))
        rms  = float(np.std(trace))
        ax.set_ylabel("µV", color="#aaaaaa", fontsize=8)
        ax.set_title(CH_LABELS[ci], color=CH_COLORS[ci], fontsize=9, loc="left")
        ax.text(0.99, 0.93,
                f"pp={pp:.1f} µV   rms={rms:.2f} µV",
                transform=ax.transAxes, ha="right", va="top",
                color="#aaaaaa", fontsize=8,
                bbox=dict(boxstyle="round,pad=0.2", fc="#1a1a1a", ec="#333333"))
        ax.tick_params(colors="#888888", labelsize=7)
        ax.spines[:].set_color("#333333")
        ax.grid(True, alpha=0.10, color="#444444")

    axes3[-1].set_xlabel("Time (s)", color="#aaaaaa")
    fig3.suptitle(f"Time-domain traces — inputs shorted\n{edf_base}",
                  color="#dddddd", fontsize=10)
    fig3.tight_layout()

    # ── Figure 4: Amplitude histograms + Gaussian fit ─────────────────────────
    ncols   = 3
    nrows   = int(np.ceil(n_active / ncols))
    fig4    = plt.figure(figsize=(5 * ncols, 4 * nrows))
    fig4.patch.set_facecolor("#111111")
    fig4.suptitle(f"Noise amplitude histograms (Gaussian fit)\n{edf_base}",
                  color="#dddddd", fontsize=10)

    for idx, ci in enumerate(active):
        ax = fig4.add_subplot(nrows, ncols, idx + 1)
        ax.set_facecolor("#111111")

        samples = seg[:, ci] - np.mean(seg[:, ci])
        mu, sigma = norm.fit(samples)

        # Histogram
        n_bins = min(120, int(np.sqrt(len(samples))))
        counts, bin_edges, _ = ax.hist(
            samples, bins=n_bins, density=True,
            color=CH_COLORS[ci], alpha=0.6, edgecolor="none")

        # Gaussian fit overlay
        x_fit = np.linspace(samples.min(), samples.max(), 400)
        ax.plot(x_fit, norm.pdf(x_fit, mu, sigma),
                color="#ffffff", lw=1.2, ls="--",
                label=f"σ={sigma:.2f} µV")

        ax.set_title(CH_LABELS[ci], color=CH_COLORS[ci], fontsize=9)
        ax.set_xlabel("µV", color="#aaaaaa", fontsize=8)
        ax.set_ylabel("Density", color="#aaaaaa", fontsize=8)
        ax.tick_params(colors="#888888", labelsize=7)
        ax.spines[:].set_color("#333333")
        ax.legend(fontsize=8, facecolor="#1a1a1a",
                  edgecolor="#333333", labelcolor="#cccccc")
        ax.grid(True, alpha=0.10, color="#444444")

    fig4.tight_layout()

    # ── Save or show ──────────────────────────────────────────────────────────
    if save_figs:
        for fig, suffix in [(fig1, "noise_psd"),
                            (fig2, "noise_rms_bar"),
                            (fig3, "noise_time"),
                            (fig4, "noise_hist")]:
            path = os.path.join(fig_dir, f"{edf_base}_{suffix}.png")
            fig.savefig(path, dpi=150, bbox_inches="tight",
                        facecolor=fig.get_facecolor())
            print(f"[SAVE]  {path}")
    else:
        plt.show()

    plt.close("all")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Input-short noise analysis for ADS1299 EEG wearable EDF recordings")
    parser.add_argument("edf", help="Path to EDF file (inputs shorted recording)")
    parser.add_argument("--trim", type=float, default=2.0,
                        help="Seconds to trim from start and end (default: 2.0)")
    parser.add_argument("--save", action="store_true",
                        help="Save figures to experiments/figures/ instead of showing")
    args = parser.parse_args()

    if not os.path.isfile(args.edf):
        print(f"[ERROR]  File not found: {args.edf}")
        sys.exit(1)

    analyse(edf_path=args.edf, trim_s=args.trim, save_figs=args.save)


if __name__ == "__main__":
    main()
