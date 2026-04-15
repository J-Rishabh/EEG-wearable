#!/usr/bin/env python3
"""
crosstalk_analysis.py — Crosstalk characterization for the ADS1299 EEG wearable
=================================================================================
Loads an EDF recording of a known sine wave injected into one channel and
quantifies how much leaks into the other channels (especially nearby ones).

Typical usage:
    python crosstalk_analysis.py ../recordings/eeg_20260411_XXXXXX.edf
    python crosstalk_analysis.py ../recordings/eeg_20260411_XXXXXX.edf --source 2 --freq 50

Arguments:
    edf             Path to the EDF file
    --source        Source (injected) channel, 1-indexed (default: 2 = CH2 EMG_far)
    --freq          Injection frequency in Hz — if omitted, auto-detected from source PSD
    --neighbors     How many channels either side to highlight as "nearby" (default: 2)
    --trim          Seconds to skip at start and end to avoid transients (default: 1)
    --save          Save figures to the figures/ directory instead of just showing them

Channel map (as stored in the EDF):
    CH1  index 0   EOG
    CH2  index 1   EMG_far        ← default injection target
    CH3  index 2   EMG_near
    CH4  index 3   EEG_L1
    CH5  index 4   EEG_L2
    CH6  index 5   EEG_L3
    CH7  index 6   SRB1_ref       (reference — usually near-zero signal)
    CH8  index 7   DRL/BIAS
"""

import sys
import os
import argparse
from typing import Optional
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.ticker import MultipleLocator
from scipy.signal import welch, windows
from scipy.fft import rfft, rfftfreq

try:
    import pyedflib
except ImportError:
    print("[ERROR] pyedflib not found.  Install with:  pip install pyEDFlib")
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
N_EEG = 8   # EEG channels in the EDF (indices 0-7)

# Colors matching the raw-channel viewer
CH_COLORS = [
    "#4fc3f7",   # CH1 EOG
    "#ef5350",   # CH2 EMG_far
    "#ffa726",   # CH3 EMG_near
    "#66bb6a",   # CH4 EEG_L1
    "#ab47bc",   # CH5 EEG_L2
    "#26c6da",   # CH6 EEG_L3
    "#90a4ae",   # CH7 SRB1_ref
    "#78909c",   # CH8 DRL
]


# ── EDF loader ────────────────────────────────────────────────────────────────

def load_edf(path: str):
    """Return (data, fs) where data is (N, 8) µV array (EEG channels only)."""
    f = pyedflib.EdfReader(path)
    try:
        n_sig = f.signals_in_file
        fs    = int(f.getSampleFrequency(0))
        # Read the first 8 channels (EEG); ignore IMU channels if present
        n_ch = min(n_sig, N_EEG)
        n_samples = f.getNSamples()[0]
        data = np.zeros((n_samples, n_ch), dtype=np.float64)
        for i in range(n_ch):
            data[:, i] = f.readSignal(i)
    finally:
        f.close()
    return data, fs


# ── Frequency detection ───────────────────────────────────────────────────────

def detect_injection_freq(sig: np.ndarray, fs: int,
                          f_min: float = 1.0, f_max: float = 120.0) -> float:
    """
    Find the dominant frequency in sig between f_min and f_max Hz.
    Uses a zero-padded FFT on the full signal for sub-Hz resolution.
    """
    win    = windows.hann(len(sig))
    spec   = np.abs(rfft(sig * win))
    freqs  = rfftfreq(len(sig), 1.0 / fs)
    mask   = (freqs >= f_min) & (freqs <= f_max)
    peak_i = np.argmax(spec[mask])
    return float(freqs[mask][peak_i])


# ── Amplitude at a specific frequency via FFT ─────────────────────────────────

def fft_amplitude_at(sig: np.ndarray, fs: int,
                     target_hz: float, bw_hz: float = 1.0) -> tuple:
    """
    Return (amplitude_uV_pp, phase_rad) of the component at target_hz ± bw_hz/2.

    amplitude_uV_pp  — peak-to-peak amplitude (2 × one-sided magnitude)
    phase_rad        — phase of the dominant bin relative to 0
    """
    win    = windows.hann(len(sig))
    spec   = rfft(sig * win)
    freqs  = rfftfreq(len(sig), 1.0 / fs)
    # Correct for Hann window amplitude loss (coherent gain = 0.5)
    spec_corr = spec / (len(sig) * 0.5)

    mask   = (freqs >= target_hz - bw_hz / 2) & (freqs <= target_hz + bw_hz / 2)
    if not np.any(mask):
        return 0.0, 0.0

    peak_i = np.argmax(np.abs(spec_corr[mask]))
    peak_c = spec_corr[mask][peak_i]

    amp_pp  = 2.0 * abs(peak_c)   # one-sided → peak-to-peak
    phase   = float(np.angle(peak_c))
    return float(amp_pp), phase


# ── Main analysis ─────────────────────────────────────────────────────────────

def analyse(edf_path: str,
            source_ch_1idx: int = 2,
            inj_freq_hz: Optional[float] = None,
            n_neighbors: int = 2,
            trim_s: float = 1.0,
            save_figs: bool = False):

    # ── Load ────────────────────────────────────────────────────────────────
    print(f"\n[LOAD]  {edf_path}")
    data, fs = load_edf(edf_path)
    n_total, n_ch = data.shape
    duration_s = n_total / fs
    print(f"        {n_total} samples × {n_ch} channels  |  fs={fs} Hz  |  {duration_s:.1f} s")

    # Trim transients at both ends
    trim_n = int(trim_s * fs)
    if 2 * trim_n >= n_total:
        print(f"[WARN]  Recording too short to trim {trim_s} s each end — skipping trim")
        trim_n = 0
    seg = data[trim_n : n_total - trim_n] if trim_n > 0 else data
    n_seg = len(seg)
    print(f"        Analysis window: {n_seg/fs:.1f} s  (trimmed {trim_s} s each end)")

    src_i = source_ch_1idx - 1   # 0-indexed
    if not (0 <= src_i < n_ch):
        print(f"[ERROR]  Source channel {source_ch_1idx} out of range (1–{n_ch})")
        sys.exit(1)

    src_sig = seg[:, src_i]

    # ── Injection frequency ──────────────────────────────────────────────────
    if inj_freq_hz is None:
        inj_freq_hz = detect_injection_freq(src_sig, fs)
        print(f"[FREQ]  Auto-detected injection frequency: {inj_freq_hz:.3f} Hz")
    else:
        print(f"[FREQ]  Using specified injection frequency: {inj_freq_hz:.3f} Hz")

    # ── Amplitude + phase at injection freq — all channels ───────────────────
    results = []   # list of (ch_1idx, label, amp_pp_uv, phase_rad, xtalk_db)
    src_amp, src_phase = fft_amplitude_at(src_sig, fs, inj_freq_hz)
    print(f"\n[SOURCE]  {CH_LABELS[src_i]}  amp = {src_amp/1000:.3f} mVpp  "
          f"({src_amp:.1f} µVpp)  phase = {np.degrees(src_phase):.1f}°")

    for ci in range(n_ch):
        amp, phase = fft_amplitude_at(seg[:, ci], fs, inj_freq_hz)
        if ci == src_i:
            xtalk_db = 0.0
        elif src_amp > 0:
            # Guard against log(0)
            xtalk_db = 20.0 * np.log10(max(amp, 1e-6) / src_amp)
        else:
            xtalk_db = float("-inf")
        # Phase relative to source
        phase_rel = np.degrees(phase - src_phase)
        # Wrap to [-180, 180]
        phase_rel = (phase_rel + 180) % 360 - 180
        results.append((ci + 1, CH_LABELS[ci], amp, phase_rel, xtalk_db))

    # ── Identify neighbors ────────────────────────────────────────────────────
    # Neighbors = channels within n_neighbors steps in index, excluding source and CH7/CH8
    neighbor_indices = set()
    for offset in range(1, n_neighbors + 1):
        for nb in (src_i - offset, src_i + offset):
            if 0 <= nb < n_ch and nb != src_i and nb < 6:   # exclude CH7/CH8
                neighbor_indices.add(nb)

    # ── Print statistics ──────────────────────────────────────────────────────
    print(f"\n{'─'*72}")
    print(f"  CROSSTALK SUMMARY  —  source: {CH_LABELS[src_i]}  "
          f"  injection: {inj_freq_hz:.2f} Hz")
    print(f"{'─'*72}")
    print(f"  {'Channel':<18}  {'Amp (µVpp)':>12}  {'Amp (mVpp)':>11}  "
          f"{'Xtalk (dB)':>11}  {'Phase rel (°)':>14}  {'Note'}")
    print(f"  {'─'*18}  {'─'*12}  {'─'*11}  {'─'*11}  {'─'*14}  {'─'*12}")
    for ch1, lbl, amp, phase_rel, db in results:
        ci = ch1 - 1
        if ci == src_i:
            note = "← SOURCE"
        elif ci in neighbor_indices:
            note = "● nearby"
        elif ci >= 6:
            note = "(ref/bias)"
        else:
            note = ""
        print(f"  {lbl:<18}  {amp:>12.2f}  {amp/1000:>11.4f}  "
              f"{db:>11.2f}  {phase_rel:>14.1f}  {note}")
    print(f"{'─'*72}\n")

    # ── Figures ───────────────────────────────────────────────────────────────
    plt.style.use("dark_background")
    fig_dir = os.path.join(os.path.dirname(__file__), "figures")
    os.makedirs(fig_dir, exist_ok=True)
    edf_base = os.path.splitext(os.path.basename(edf_path))[0]

    # ── Figure 1: PSD of all channels ────────────────────────────────────────
    fig1, ax1 = plt.subplots(figsize=(12, 6))
    fig1.patch.set_facecolor("#111111")
    ax1.set_facecolor("#111111")

    nperseg = min(4 * fs, n_seg)
    for ci in range(min(n_ch, 7)):   # skip CH8 DRL
        f_psd, pxx = welch(seg[:, ci], fs=fs,
                           nperseg=nperseg,
                           noverlap=nperseg // 2,
                           window="hann")
        lw   = 2.0 if ci == src_i else 1.0
        ls   = "-"  if ci == src_i else ("--" if ci in neighbor_indices else ":")
        zord = 3    if ci == src_i else (2 if ci in neighbor_indices else 1)
        alpha = 1.0 if (ci == src_i or ci in neighbor_indices) else 0.45
        ax1.semilogy(f_psd, pxx,
                     color=CH_COLORS[ci], lw=lw, ls=ls,
                     alpha=alpha, zorder=zord,
                     label=CH_LABELS[ci] + (" ← source" if ci == src_i else
                                            " ●" if ci in neighbor_indices else ""))

    ax1.axvline(inj_freq_hz, color="#ffd54f", lw=1.2, ls="--",
                label=f"f_inj = {inj_freq_hz:.2f} Hz")
    ax1.set_xlabel("Frequency (Hz)", color="#aaaaaa")
    ax1.set_ylabel("PSD (µV² / Hz)", color="#aaaaaa")
    ax1.set_title(f"Power Spectral Density — all channels\n"
                  f"{edf_base}  |  source: {CH_LABELS[src_i]}  "
                  f"  f_inj = {inj_freq_hz:.2f} Hz",
                  color="#dddddd")
    ax1.set_xlim(0, min(120, fs / 2))
    ax1.tick_params(colors="#888888")
    ax1.spines[:].set_color("#333333")
    ax1.legend(loc="upper right", fontsize=8,
               facecolor="#1a1a1a", edgecolor="#333333", labelcolor="#cccccc")
    ax1.grid(True, alpha=0.15, color="#444444")
    fig1.tight_layout()

    # ── Figure 2: Crosstalk bar chart ─────────────────────────────────────────
    # Only non-source EEG channels (skip CH7 SRB1 and CH8 DRL)
    bar_results = [(r[1], r[4], r[0]-1) for r in results
                   if r[0]-1 != src_i and r[0]-1 < 6]
    labels_b  = [r[0] for r in bar_results]
    xtalk_b   = [r[1] for r in bar_results]
    ci_b      = [r[2] for r in bar_results]
    colors_b  = [CH_COLORS[ci] if ci in neighbor_indices
                 else "#555555" for ci in ci_b]

    fig2, ax2 = plt.subplots(figsize=(9, 5))
    fig2.patch.set_facecolor("#111111")
    ax2.set_facecolor("#111111")

    bars = ax2.barh(labels_b, xtalk_b, color=colors_b, edgecolor="#2a2a2a", height=0.6)
    # Annotate bar values
    for bar, val in zip(bars, xtalk_b):
        ax2.text(val - 1.5 if val < -5 else val + 0.5,
                 bar.get_y() + bar.get_height() / 2,
                 f"{val:.1f} dB",
                 va="center", ha="right" if val < -5 else "left",
                 color="#cccccc", fontsize=8)

    # Reference lines
    for ref_db, label in [(-20, "−20 dB"), (-40, "−40 dB"), (-60, "−60 dB")]:
        ax2.axvline(ref_db, color="#444444", lw=0.8, ls="--")
        ax2.text(ref_db, len(labels_b) - 0.1, label,
                 color="#555555", fontsize=7, ha="center", va="bottom")

    ax2.set_xlabel("Crosstalk (dB re source)", color="#aaaaaa")
    ax2.set_title(f"Crosstalk at f_inj = {inj_freq_hz:.2f} Hz\n"
                  f"source: {CH_LABELS[src_i]}  |  ● = nearby channel",
                  color="#dddddd")
    ax2.tick_params(colors="#888888")
    ax2.spines[:].set_color("#333333")
    ax2.grid(True, axis="x", alpha=0.12, color="#444444")

    # Legend for nearby vs far
    from matplotlib.patches import Patch
    legend_els = [
        Patch(facecolor=CH_COLORS[list(neighbor_indices)[0]] if neighbor_indices
              else "#888888", label="nearby channel"),
        Patch(facecolor="#555555", label="far channel"),
    ]
    ax2.legend(handles=legend_els, loc="lower right", fontsize=8,
               facecolor="#1a1a1a", edgecolor="#333333", labelcolor="#cccccc")
    fig2.tight_layout()

    # ── Figure 3: Time-domain — source vs nearby channels ────────────────────
    # Show 5 cycles of the injection frequency
    n_cycles = 5
    cycle_samples = int(n_cycles * fs / inj_freq_hz)
    # Start from 10% into the trimmed segment to avoid any initial transient
    t_start = max(0, n_seg // 10)
    t_end   = min(t_start + cycle_samples, n_seg)
    t_ax    = np.arange(t_end - t_start) / fs * 1000   # ms

    plot_chs = [src_i] + sorted(neighbor_indices)
    n_plot   = len(plot_chs)

    fig3, axes3 = plt.subplots(n_plot, 1, figsize=(12, 3 * n_plot),
                               sharex=True, sharey=False)
    fig3.patch.set_facecolor("#111111")
    if n_plot == 1:
        axes3 = [axes3]

    for ax, ci in zip(axes3, plot_chs):
        ax.set_facecolor("#111111")
        trace = seg[t_start:t_end, ci]
        # Remove DC for display
        trace = trace - np.mean(trace)
        ax.plot(t_ax, trace, color=CH_COLORS[ci], lw=1.2)
        lbl = CH_LABELS[ci] + (" ← SOURCE" if ci == src_i else " (neighbor)")
        ax.set_ylabel("µV", color="#aaaaaa", fontsize=8)
        ax.set_title(lbl, color=CH_COLORS[ci], fontsize=9, loc="left")
        ax.tick_params(colors="#888888", labelsize=7)
        ax.spines[:].set_color("#333333")
        ax.grid(True, alpha=0.12, color="#444444")

        # Annotate amplitude
        pp = float(np.max(trace) - np.min(trace))
        ax.text(0.99, 0.95, f"pp ≈ {pp:.1f} µV",
                transform=ax.transAxes, ha="right", va="top",
                color="#aaaaaa", fontsize=8,
                bbox=dict(boxstyle="round,pad=0.2", fc="#1a1a1a", ec="#333333"))

    axes3[-1].set_xlabel(f"Time (ms)  —  {n_cycles} cycles @ {inj_freq_hz:.2f} Hz",
                         color="#aaaaaa")
    fig3.suptitle(f"Time-domain: source vs neighbors\n{edf_base}",
                  color="#dddddd", fontsize=10)
    fig3.tight_layout()

    # ── Figure 4: Overlay — source + neighbors at matched scale ──────────────
    fig4, ax4 = plt.subplots(figsize=(12, 4))
    fig4.patch.set_facecolor("#111111")
    ax4.set_facecolor("#111111")

    src_trace = seg[t_start:t_end, src_i]
    src_trace = src_trace - np.mean(src_trace)

    for ci in plot_chs:
        trace = seg[t_start:t_end, ci]
        trace = trace - np.mean(trace)
        lw    = 2.0 if ci == src_i else 1.0
        alpha = 1.0 if ci == src_i else 0.8
        # Scale neighbor traces to source amplitude for phase-alignment view
        amp_ratio = fft_amplitude_at(seg[:, ci], fs, inj_freq_hz)[0] / max(src_amp, 1e-6)
        lbl = (f"{CH_LABELS[ci]}  (source, {src_amp/1000:.2f} mVpp)"
               if ci == src_i else
               f"{CH_LABELS[ci]}  ({amp_ratio*100:.3f}% of source, "
               f"{results[ci][4]:.1f} dB)")
        ax4.plot(t_ax, trace, color=CH_COLORS[ci], lw=lw, alpha=alpha, label=lbl)

    ax4.set_xlabel(f"Time (ms)  —  {n_cycles} cycles @ {inj_freq_hz:.2f} Hz",
                   color="#aaaaaa")
    ax4.set_ylabel("µV  (DC removed)", color="#aaaaaa")
    ax4.set_title(f"Overlay — source & neighbors at raw scale\n{edf_base}",
                  color="#dddddd")
    ax4.tick_params(colors="#888888")
    ax4.spines[:].set_color("#333333")
    ax4.legend(loc="upper right", fontsize=8,
               facecolor="#1a1a1a", edgecolor="#333333", labelcolor="#cccccc")
    ax4.grid(True, alpha=0.12, color="#444444")
    fig4.tight_layout()

    # ── Save or show ─────────────────────────────────────────────────────────
    if save_figs:
        for fig, suffix in [(fig1, "psd"), (fig2, "xtalk_bar"),
                            (fig3, "time_traces"), (fig4, "overlay")]:
            path = os.path.join(fig_dir, f"{edf_base}_xtalk_{suffix}.png")
            fig.savefig(path, dpi=150, bbox_inches="tight",
                        facecolor=fig.get_facecolor())
            print(f"[SAVE]  {path}")
    else:
        plt.show()

    plt.close("all")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Crosstalk analysis for ADS1299 EEG wearable EDF recordings")
    parser.add_argument("edf", help="Path to EDF file")
    parser.add_argument("--source", type=int, default=2,
                        help="Source (injected) channel, 1-indexed (default: 2 = CH2)")
    parser.add_argument("--freq", type=float, default=None,
                        help="Injection frequency in Hz (default: auto-detect)")
    parser.add_argument("--neighbors", type=int, default=2,
                        help="Channels either side to highlight as nearby (default: 2)")
    parser.add_argument("--trim", type=float, default=1.0,
                        help="Seconds to trim from start and end (default: 1.0)")
    parser.add_argument("--save", action="store_true",
                        help="Save figures to experiments/figures/ instead of showing")
    args = parser.parse_args()

    if not os.path.isfile(args.edf):
        print(f"[ERROR]  File not found: {args.edf}")
        sys.exit(1)

    analyse(
        edf_path       = args.edf,
        source_ch_1idx = args.source,
        inj_freq_hz    = args.freq,
        n_neighbors    = args.neighbors,
        trim_s         = args.trim,
        save_figs      = args.save,
    )


if __name__ == "__main__":
    main()
