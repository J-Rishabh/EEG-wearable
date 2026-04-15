#!/usr/bin/env python3
"""
edf_viewer.py — EDF analysis & thesis figure generator
=======================================================
Tkinter GUI that loads EEG recordings saved by eeg_stream_pg.py,
optionally loads BCI event logs, and exports publication-quality plots.

Requirements:
    pip install pyedflib numpy scipy matplotlib

Run:
    python edf_viewer.py
    python edf_viewer.py --edf ../recordings/eeg_20260406_212736.edf
"""

from __future__ import annotations

import os
import sys
import json
import glob
import argparse
import threading
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from scipy.signal import butter, iirnotch, sosfilt, sosfiltfilt, tf2sos, welch, spectrogram

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ── Paths ─────────────────────────────────────────────────────────────────────

_HERE        = os.path.dirname(os.path.abspath(__file__))
RECORDINGS_DIR = os.path.normpath(os.path.join(_HERE, "..", "recordings"))
EVENTS_DIR     = os.path.join(_HERE, "bci", "events")
FIGURES_DIR    = os.path.join(_HERE, "figures")

# ── Matplotlib style ──────────────────────────────────────────────────────────

# Thesis-quality defaults — clean, no chartjunk
plt.rcParams.update({
    "figure.dpi":        150,
    "savefig.dpi":       300,
    "font.family":       "serif",
    "font.size":         11,
    "axes.titlesize":    12,
    "axes.labelsize":    11,
    "xtick.labelsize":   9,
    "ytick.labelsize":   9,
    "legend.fontsize":   9,
    "lines.linewidth":   1.0,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.grid":         True,
    "grid.alpha":        0.3,
    "figure.facecolor":  "white",
})

CHANNEL_COLORS = {
    "EOG":     "#4fc3f7",
    "EMG_far": "#ff8a65",
    "EMG_near":"#ffb74d",
    "EEG_L1":  "#81c784",
    "EEG_L2":  "#4db6ac",
    "EEG_L3":  "#7986cb",
    "SRB1":    "#a1887f",
    "DRL":     "#90a4ae",
}


# ── EDF loading ───────────────────────────────────────────────────────────────

class Recording:
    """Holds all data for one EDF session."""
    def __init__(self, edf_path: str):
        import pyedflib
        f = pyedflib.EdfReader(edf_path)
        try:
            self.labels   = list(f.getSignalLabels())
            n             = f.signals_in_file
            fs_vals       = [int(f.getSampleFrequency(i)) for i in range(n)]
            self.fs       = fs_vals[0]
            self.fs_per_ch = fs_vals
            # Store as list of 1-D arrays — channels have different lengths when
            # EEG (250 Hz) and IMU (25 Hz) are mixed in the same EDF.
            self.data     = [np.array(f.readSignal(i), dtype=np.float64)
                             for i in range(n)]
            self.start_dt = f.getStartdatetime()        # datetime or None
        finally:
            f.close()
        self.path         = edf_path
        self.n_samples    = len(self.data[0])
        self.duration_s   = self.n_samples / self.fs
        # Try to load matching meta.json for rec_start_epoch + packet times
        self.rec_start_epoch = None
        self.pkt_times       = None   # float64 array of BLE packet arrival times, or None
        self._load_meta()

    def _load_meta(self):
        stem      = os.path.splitext(os.path.basename(self.path))[0]
        rec_dir   = os.path.dirname(self.path)
        meta_path = os.path.join(rec_dir, f"{stem}_meta.json")
        if not os.path.exists(meta_path):
            return
        with open(meta_path) as f:
            meta = json.load(f)
        self.rec_start_epoch = meta.get("rec_start_epoch")
        pkt_file = meta.get("packet_times_file")
        if pkt_file:
            pkt_path = os.path.join(rec_dir, pkt_file)
            if os.path.exists(pkt_path):
                self.pkt_times = np.load(pkt_path)

    def channel_index(self, label: str) -> int | None:
        for i, l in enumerate(self.labels):
            if l.strip() == label.strip():
                return i
        return None

    def time_slice(self, channels: list[str], t_start: float, t_end: float):
        """Return (data_slice, t_array) for given channel labels and time window."""
        s0 = max(0, int(t_start * self.fs))
        s1 = min(self.n_samples, int(t_end   * self.fs))
        t  = np.arange(s0, s1) / self.fs
        rows = []
        for ch in channels:
            idx = self.channel_index(ch)
            if idx is not None:
                rows.append(self.data[idx][s0:s1])
        return np.array(rows), t


# ── Signal processing helpers ─────────────────────────────────────────────────

def _hp_filter(data: np.ndarray, fs: int, cutoff: float) -> np.ndarray:
    sos = butter(2, cutoff / (fs / 2), btype="high", output="sos")
    return sosfiltfilt(sos, data, axis=-1)

def _lp_filter(data: np.ndarray, fs: int, cutoff: float) -> np.ndarray:
    sos = butter(4, cutoff / (fs / 2), btype="low", output="sos")
    return sosfiltfilt(sos, data, axis=-1)

def _notch_filter(data: np.ndarray, fs: int, freq: float = 60.0) -> np.ndarray:
    b, a = iirnotch(freq, Q=30, fs=fs)
    sos  = tf2sos(b, a)
    return sosfiltfilt(sos, data, axis=-1)

def _apply_filters(data: np.ndarray, fs: int,
                   hp: float | None, lp: float | None,
                   notch: bool) -> np.ndarray:
    out = data.copy()
    try:
        if hp:
            out = _hp_filter(out, fs, hp)
        if lp:
            out = _lp_filter(out, fs, lp)
        if notch:
            out = _notch_filter(out, fs)
    except ValueError:
        # Signal too short for sosfiltfilt (padlen > n_samples) — return unfiltered
        return data.copy()
    return out


# ── Plot functions ─────────────────────────────────────────────────────────────

def _parse_derived_channels(
    rec: Recording, exprs_text: str, t_start: float, t_end: float
) -> list[tuple[str, np.ndarray]]:
    """
    Parse newline-separated derivation expressions and return raw data arrays.

    Each line is a Python expression using channel label names, e.g.:
        EEG_L1 - EEG_L3
        0.5 * EEG_L1 + 0.5 * EEG_L2

    Optionally prefix with a custom y-axis label separated by ' : ', e.g.:
        Occipital bipolar : EEG_L1 - EEG_L3

    Channel names are mapped to safe Python identifiers before eval().
    Returns list of (label, raw_array) — filters are applied later by plot_traces/plot_psd.
    """
    import re
    s0 = max(0, int(t_start * rec.fs))
    s1 = min(rec.n_samples, int(t_end * rec.fs))

    # Map each channel label to a safe Python identifier and load its data slice
    safe_map: dict[str, str] = {}
    ns: dict[str, np.ndarray] = {}
    for label in rec.labels:
        key = label.strip()
        safe = re.sub(r"\W", "_", key)
        safe_map[key] = safe
        idx = rec.channel_index(label)
        if idx is not None:
            ns[safe] = rec.data[idx][s0:s1].copy()

    results: list[tuple[str, np.ndarray]] = []
    for line in exprs_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Optional  "My label : expression"  syntax
        if " : " in line:
            row_label, expr_raw = line.split(" : ", 1)
            row_label = row_label.strip()
        else:
            row_label = line
            expr_raw  = line
        expr = expr_raw
        # Replace channel names longest-first to avoid partial matches
        for orig in sorted(safe_map, key=len, reverse=True):
            expr = expr.replace(orig, safe_map[orig])
        try:
            arr = eval(expr, {"__builtins__": {}, "np": np}, ns)  # noqa: S307
            results.append((row_label, np.asarray(arr, dtype=np.float64)))
        except Exception as e:
            raise ValueError(f"Cannot evaluate '{expr_raw}': {e}") from e
    return results


def plot_traces(rec: Recording, channels: list[str],
                t_start: float, t_end: float,
                hp: float | None, lp: float | None, notch: bool,
                title_suffix: str = "",
                extras: list[tuple[str, np.ndarray]] | None = None) -> plt.Figure:
    """Time-domain traces for selected channels and window."""
    data, t = rec.time_slice(channels, t_start, t_end)
    data = _apply_filters(data, rec.fs, hp, lp, notch)

    all_labels = list(channels)
    all_rows   = list(data)

    if extras:
        for label, arr in extras:
            filt = _apply_filters(arr[np.newaxis, :], rec.fs, hp, lp, notch)[0]
            all_labels.append(label)
            all_rows.append(filt)

    n_ch = len(all_labels)
    fig, axes = plt.subplots(n_ch, 1, figsize=(12, 1.8 * n_ch + 0.5),
                             sharex=True)
    if n_ch == 1:
        axes = [axes]

    filt_str = []
    if hp:   filt_str.append(f"HP {hp} Hz")
    if lp:   filt_str.append(f"LP {lp} Hz")
    if notch: filt_str.append("60 Hz notch")
    filt_label = " | ".join(filt_str) if filt_str else "unfiltered"

    fig.suptitle(f"EEG traces — {filt_label}{title_suffix}", fontweight="bold")

    for ax, ch, row in zip(axes, all_labels, all_rows):
        color = CHANNEL_COLORS.get(ch, "#f38ba8")   # pink for derived channels
        ax.plot(t, row, color=color, linewidth=0.7)
        ax.set_ylabel(f"{ch.replace('_', ' ')}\n(uV)", fontsize=9)
        ax.set_xlim(t[0], t[-1])
        # Auto-scale per channel
        p1, p99 = np.percentile(row, [1, 99])
        margin = max(5.0, (p99 - p1) * 0.2)
        ax.set_ylim(p1 - margin, p99 + margin)

    axes[-1].set_xlabel("Time (s)")
    fig.tight_layout()
    return fig


def plot_psd(rec: Recording, channels: list[str],
             t_start: float, t_end: float,
             hp: float | None, lp: float | None, notch: bool,
             per_channel: bool = True,
             extras: list[tuple[str, np.ndarray]] | None = None) -> plt.Figure:
    """Welch power spectral density."""
    data, _ = rec.time_slice(channels, t_start, t_end)
    data = _apply_filters(data, rec.fs, hp, lp, notch)

    all_labels = list(channels)
    all_rows   = list(data)

    if extras:
        for label, arr in extras:
            filt = _apply_filters(arr[np.newaxis, :], rec.fs, hp, lp, notch)[0]
            all_labels.append(label)
            all_rows.append(filt)

    nperseg = min(rec.fs * 4, len(all_rows[0]) if all_rows else rec.fs * 4)
    fig, ax = plt.subplots(figsize=(9, 5))

    for ch, row in zip(all_labels, all_rows):
        f, psd = welch(row, fs=rec.fs, nperseg=nperseg)
        color  = CHANNEL_COLORS.get(ch, "#888888")
        if per_channel:
            ax.semilogy(f, psd, color=color, label=ch)
        else:
            ax.semilogy(f, psd, color=color, alpha=0.3, linewidth=0.8)

    if not per_channel and len(all_rows) > 0:
        # Compute and plot average PSD
        psds = [welch(row, fs=rec.fs, nperseg=nperseg)[1] for row in all_rows]
        f, _ = welch(all_rows[0], fs=rec.fs, nperseg=nperseg)
        ax.semilogy(f, np.mean(psds, axis=0), color="black",
                    linewidth=1.5, label="mean")

    # Annotate canonical EEG bands
    bands = {"δ": (0.5, 4), "θ": (4, 8), "α": (8, 13), "β": (13, 30), "γ": (30, 45)}
    colors_b = ["#e3f2fd", "#e8f5e9", "#fff9c4", "#fce4ec", "#f3e5f5"]
    ymin, ymax = ax.get_ylim()
    for (name, (lo, hi)), bc in zip(bands.items(), colors_b):
        ax.axvspan(lo, hi, alpha=0.12, color=bc, label=None)
        ax.text((lo + hi) / 2, ymax, name, ha="center", va="top",
                fontsize=8, color="#555555")

    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("PSD (uV²/Hz)")
    ax.set_xlim(0, min(rec.fs / 2, 80))
    ax.set_title("Power spectral density (Welch)", fontweight="bold")
    if per_channel:
        ax.legend(framealpha=0.7)
    fig.tight_layout()
    return fig


def plot_spectrogram_fig(rec: Recording, channel: str,
                         t_start: float, t_end: float,
                         hp: float | None, lp: float | None,
                         notch: bool) -> plt.Figure:
    """Time-frequency spectrogram for one channel."""
    idx = rec.channel_index(channel)
    if idx is None:
        raise ValueError(f"Channel {channel!r} not found")
    s0 = int(t_start * rec.fs)
    s1 = int(t_end   * rec.fs)
    raw = rec.data[idx][s0:s1]
    raw = _apply_filters(raw[np.newaxis, :], rec.fs, hp, lp, notch)[0]

    nperseg = min(rec.fs, len(raw) // 4)
    f, t_sg, Sxx = spectrogram(raw, fs=rec.fs, nperseg=nperseg,
                                noverlap=nperseg // 2)
    t_sg += t_start  # align to recording time

    fig, (ax_sig, ax_sg) = plt.subplots(2, 1, figsize=(12, 6),
                                         gridspec_kw={"height_ratios": [1, 2.5]})
    t_full = np.arange(s0, s1) / rec.fs
    ax_sig.plot(t_full, raw, color=CHANNEL_COLORS.get(channel, "#888"), linewidth=0.6)
    ax_sig.set_ylabel("uV")
    ax_sig.set_xlim(t_start, t_end)
    ax_sig.set_title(f"Spectrogram — {channel}", fontweight="bold")

    im = ax_sg.pcolormesh(t_sg, f[f <= 60], 10 * np.log10(Sxx[f <= 60] + 1e-30),
                           shading="gouraud", cmap="viridis")
    ax_sg.set_ylabel("Frequency (Hz)")
    ax_sg.set_xlabel("Time (s)")
    ax_sg.set_xlim(t_start, t_end)
    plt.colorbar(im, ax=ax_sg, label="Power (dB)")
    fig.tight_layout()
    return fig


def plot_erp(rec: Recording, channels: list[str],
             events_df, rec_start: float,
             tmin: float = -0.2, tmax: float = 0.8,
             hp_hz: float = 1.0,
             audio_latency_s: float = 0.006,
             reject_uv: float = 75.0,
             use_pkt_timing: bool = False) -> plt.Figure:
    """
    P300 ERP: epoch around deviant onsets, average with SEM band.
    events_df must have columns: wall_time, event  (values: 'deviant', 'standard').
    rec_start: time.time() epoch when EEG recording started.

    hp_hz: high-pass cutoff applied before epoching to remove slow drift.
    audio_latency_s: pygame mixer buffer latency — shifts epoch onset forward
                     so t=0 aligns with actual sound delivery (~6 ms for buffer=256).

    Timing: if rec.pkt_times is available (saved by eeg_stream_pg.py), a linear
    fit over BLE packet arrival times is used to map wall-clock → sample index.
    This removes BLE delivery jitter (~15-130 ms) that would otherwise smear ERPs.
    Falls back to rec_start + n/fs if no packet times file exists.
    """
    import pandas as pd

    deviant_times  = events_df.loc[events_df["event"] == "deviant",  "wall_time"].values
    standard_times = events_df.loc[events_df["event"] == "standard", "wall_time"].values

    fs = rec.fs
    n_pre  = int(abs(tmin) * fs)
    n_post = int(tmax * fs)
    n_epoch = n_pre + n_post
    t_ep = np.linspace(tmin, tmax, n_epoch)

    # Build wall-clock → sample-index converter.
    # If packet arrival times exist, fit a line through (packet_index, t_arrival)
    # to get slope (s/packet) and intercept. This corrects for:
    #   • the gap between rec_start and first packet (BLE connection delay)
    #   • slow clock drift between device crystal and PC system clock
    # The per-packet jitter is averaged out by the fit.
    timing_note = "timing: rec_start fallback"
    if use_pkt_timing and rec.pkt_times is not None and len(rec.pkt_times) > 10:
        pkt_idx = np.arange(len(rec.pkt_times), dtype=np.float64)
        slope, intercept = np.polyfit(pkt_idx, rec.pkt_times, 1)
        # t_arrival[k] ≈ intercept + slope*k  (slope ≈ 8/fs = 0.032 s/packet)
        # Sample n is in packet n//8; assign t_sample[n] = intercept + slope*(n/8)
        samples_per_pkt = float(fs) * slope  # should be ~8.0
        def _wall_to_sample(t_wall):
            # invert: n/8 = (t_wall - intercept) / slope  →  n = 8*(t_wall-intercept)/slope
            return int(8.0 * (t_wall - intercept) / slope)
        timing_note = (f"timing: pkt fit  slope={slope*1000:.1f} ms/pkt "
                       f"(ideal {8000/fs:.1f})  n_pkts={len(rec.pkt_times)}")
    else:
        def _wall_to_sample(t_wall):
            return int((t_wall - rec_start) * fs)
    print(f"[ERP] {timing_note}")

    # Pre-filter each channel with HP to remove slow electrode drift before epoching.
    # Done on the full signal so filter transients don't land inside epochs.
    filtered = {}
    for ch in channels:
        idx = rec.channel_index(ch)
        if idx is not None:
            filtered[ch] = _hp_filter(rec.data[idx], fs, hp_hz)

    n_rejected = [0, 0]  # [deviant, standard] rejection counts

    def _epoch(onset_times, reject_idx=0):
        epochs = []
        for wt in onset_times:
            # Shift onset by audio pipeline latency so t=0 = sound at ears
            t_onset = wt + audio_latency_s
            s0 = _wall_to_sample(t_onset) - n_pre
            s1 = s0 + n_epoch
            if s0 < 0 or s1 > rec.n_samples:
                continue
            rows = []
            for ch in channels:
                if ch in filtered:
                    rows.append(filtered[ch][s0:s1])
            if rows:
                ep = np.array(rows)
                # Baseline correct to pre-stimulus window
                bl = ep[:, :n_pre].mean(axis=1, keepdims=True)
                ep = ep - bl
                # Artifact rejection — drop epoch if peak-to-peak on any channel
                # exceeds threshold (catches coughs, movement, electrode pop)
                ptp = ep.max(axis=1) - ep.min(axis=1)
                if np.any(ptp > reject_uv):
                    n_rejected[reject_idx] += 1
                    continue
                epochs.append(ep)
        return np.array(epochs) if epochs else None   # (n_epochs, n_ch, n_samples)

    dev_ep  = _epoch(deviant_times,  reject_idx=0)
    std_ep  = _epoch(standard_times, reject_idx=1)
    print(f"[ERP] Rejected: {n_rejected[0]} deviant, {n_rejected[1]} standard  "
          f"(threshold ±{reject_uv} uV p-p)")

    n_ch = len(channels)
    fig, axes = plt.subplots(1, n_ch, figsize=(4.5 * n_ch, 4.5), sharey=True)
    if n_ch == 1:
        axes = [axes]

    timing_label = "pkt-fit timing" if (use_pkt_timing and rec.pkt_times is not None and len(rec.pkt_times) > 10) else "rec_start timing"
    fig.suptitle(
        f"ERP — P300 (deviant vs standard)  |  HP {hp_hz} Hz  |  "
        f"audio +{int(audio_latency_s*1000)} ms  |  {timing_label}  |  "
        f"rejected: {n_rejected[0]}dev {n_rejected[1]}std (>{reject_uv:.0f}uV)",
        fontweight="bold", fontsize=9)

    for ax, ch in zip(axes, channels):
        color = CHANNEL_COLORS.get(ch, "#888")
        if dev_ep is not None and len(dev_ep):
            ch_i = channels.index(ch)
            mn   = dev_ep[:, ch_i, :].mean(axis=0)
            sem  = dev_ep[:, ch_i, :].std(axis=0) / np.sqrt(len(dev_ep))
            ax.fill_between(t_ep, mn - sem, mn + sem, color=color, alpha=0.25)
            ax.plot(t_ep, mn, color=color, linewidth=1.4,
                    label=f"Deviant (n={len(dev_ep)})")
        if std_ep is not None and len(std_ep):
            ch_i = channels.index(ch)
            mn   = std_ep[:, ch_i, :].mean(axis=0)
            ax.plot(t_ep, mn, color="#aaaaaa", linewidth=1.0, linestyle="--",
                    label=f"Standard (n={len(std_ep)})")
        ax.axvline(0, color="black", linewidth=0.8, linestyle=":")
        ax.axhline(0, color="#cccccc", linewidth=0.6)
        ax.set_xlabel("Time (s)")
        ax.set_title(ch)
        ax.legend(fontsize=8)

    axes[0].set_ylabel("Amplitude (uV)")
    fig.tight_layout()
    return fig


def plot_ssvep_snr(rec: Recording, channels: list[str],
                   events_df, rec_start: float,
                   warmup_s: float = 2.0,
                   snr_bw_hz: float = 0.5) -> plt.Figure:
    """
    SSVEP SNR bar chart: for each target frequency and each channel,
    compute peak / flanking-noise SNR and plot as grouped bars.
    Includes 2nd and 3rd harmonics.
    """
    flicker_starts  = events_df[events_df["event"] == "FLICKER_START"].copy()
    flicker_ends    = events_df[events_df["event"] == "FLICKER_END"].copy()
    analysis_starts = events_df[events_df["event"] == "ANALYSIS_START"].copy() \
                      if "ANALYSIS_START" in events_df["event"].values else None

    if flicker_starts.empty:
        raise ValueError("No FLICKER_START events found in event log")

    target_freqs = sorted(flicker_starts["freq_hz"].dropna().unique())
    NPERSEG      = int(rec.fs * 4)   # fixed across all epochs so PSDs are same length

    def _snr(psd_arr, f_arr, target_hz, bw):
        df = f_arr[1] - f_arr[0]
        peak_mask  = np.abs(f_arr - target_hz) < df * 0.6
        flank_mask = (np.abs(f_arr - target_hz) <= bw) & ~peak_mask
        if not peak_mask.any() or not flank_mask.any():
            return np.nan
        noise = np.mean(psd_arr[flank_mask])
        return float(np.mean(psd_arr[peak_mask]) / noise) if noise > 1e-30 else np.nan

    # Compute per-freq, per-channel mean PSD
    results = {}   # freq -> {ch: mean_psd, f_axis}
    for freq in target_freqs:
        rows_s = flicker_starts[flicker_starts["freq_hz"] == freq].reset_index(drop=True)
        rows_e = flicker_ends[flicker_ends["freq_hz"] == freq].reset_index(drop=True) \
                 if not flicker_ends.empty else None
        rows_a = analysis_starts[analysis_starts["freq_hz"] == freq].reset_index(drop=True) \
                 if analysis_starts is not None and not analysis_starts.empty else None

        ch_psds = {ch: [] for ch in channels}
        f_axis  = None
        for i, row in rows_s.iterrows():
            if rows_a is not None and i < len(rows_a):
                t0 = rows_a.iloc[i]["wall_time"] - rec_start
            else:
                t0 = row["wall_time"] - rec_start + warmup_s
            t1 = row["wall_time"] - rec_start + 12.0
            if rows_e is not None and i < len(rows_e):
                t1 = rows_e.iloc[i]["wall_time"] - rec_start
            s0 = max(0, int(t0 * rec.fs))
            s1 = min(rec.n_samples, int(t1 * rec.fs))
            if s1 - s0 < NPERSEG:   # skip epochs too short for the fixed nperseg
                continue
            for ch in channels:
                idx = rec.channel_index(ch)
                if idx is None:
                    continue
                seg = rec.data[idx][s0:s1]
                f_seg, psd = welch(seg, fs=rec.fs, nperseg=NPERSEG)
                ch_psds[ch].append(psd)
                if f_axis is None:
                    f_axis = f_seg
        results[freq] = {"ch_psds": ch_psds, "f_axis": f_axis}

    harmonics = [1, 2, 3]
    n_freq    = len(target_freqs)
    n_harm    = len(harmonics)
    n_ch      = len(channels)

    fig, axes = plt.subplots(1, n_freq, figsize=(4.5 * n_freq, 4.5), sharey=False)
    if n_freq == 1:
        axes = [axes]

    fig.suptitle("SSVEP SNR — peak / flanking-noise ratio per channel and harmonic",
                 fontweight="bold")

    bar_w   = 0.8 / n_ch
    x_base  = np.arange(n_harm)   # one group per harmonic

    for ax, freq in zip(axes, target_freqs):
        res = results[freq]
        if res["f_axis"] is None:
            ax.set_title(f"{freq:.0f} Hz — no data")
            continue

        for ci, ch in enumerate(channels):
            if not res["ch_psds"][ch]:
                continue
            color    = CHANNEL_COLORS.get(ch, "#888")
            mean_psd = np.mean(res["ch_psds"][ch], axis=0)
            snr_vals = [_snr(mean_psd, res["f_axis"], freq * h, snr_bw_hz)
                        for h in harmonics]
            x_pos = x_base + (ci - n_ch / 2 + 0.5) * bar_w
            bars  = ax.bar(x_pos, snr_vals, width=bar_w * 0.9,
                           color=color, alpha=0.82, label=ch)

        ax.axhline(1.0, color="#888", linewidth=0.8, linestyle="--")
        ax.set_xticks(x_base)
        ax.set_xticklabels([f"{freq * h:.0f} Hz\n({h}f)" for h in harmonics])
        ax.set_xlabel("Harmonic")
        ax.set_title(f"Stimulus: {freq:.0f} Hz  (n={len(flicker_starts[flicker_starts['freq_hz']==freq])} epochs)")
        ax.legend(fontsize=7, framealpha=0.7)

    axes[0].set_ylabel("SNR (peak / noise floor)")
    fig.tight_layout()
    return fig


def plot_ssvep(rec: Recording, channels: list[str],
               events_df, rec_start: float,
               warmup_s: float = 2.0,
               snr_bw_hz: float = 0.5) -> plt.Figure:
    """
    SSVEP: Welch PSD of flicker epochs per target frequency, averaged across runs.

    events_df columns: wall_time, freq_hz, event.
    Epochs start at ANALYSIS_START if present (skips warmup transient), otherwise
    WARMUP_SEC=2s after FLICKER_START, so the visual cortex has time to entrain.

    SNR is computed as peak power at each harmonic / mean of flanking noise bins
    (±snr_bw_hz Hz either side, excluding the peak bin itself).
    """
    flicker_starts  = events_df[events_df["event"] == "FLICKER_START"].copy()
    flicker_ends    = events_df[events_df["event"] == "FLICKER_END"].copy()
    analysis_starts = events_df[events_df["event"] == "ANALYSIS_START"].copy() \
                      if "ANALYSIS_START" in events_df["event"].values else None

    if flicker_starts.empty:
        raise ValueError("No FLICKER_START events found in event log")

    target_freqs = sorted(flicker_starts["freq_hz"].dropna().unique())
    n_freqs = len(target_freqs)

    fig, axes = plt.subplots(1, n_freqs, figsize=(4.5 * n_freqs, 4.5), sharey=True)
    if n_freqs == 1:
        axes = [axes]

    fig.suptitle("SSVEP — mean Welch PSD per stimulus frequency (occipital channels)",
                 fontweight="bold")

    def _snr(psd_arr, f_arr, target_hz, bw):
        """Peak / mean-of-flanks SNR at target_hz."""
        peak_mask = np.abs(f_arr - target_hz) < (f_arr[1] - f_arr[0]) * 0.6
        flank_mask = (np.abs(f_arr - target_hz) <= bw) & ~peak_mask
        if not peak_mask.any() or not flank_mask.any():
            return np.nan
        noise = np.mean(psd_arr[flank_mask])
        if noise < 1e-30:
            return np.nan
        return float(np.mean(psd_arr[peak_mask]) / noise)

    for ax, freq in zip(axes, target_freqs):
        rows_s  = flicker_starts[flicker_starts["freq_hz"] == freq].reset_index(drop=True)
        rows_e  = flicker_ends[flicker_ends["freq_hz"] == freq].reset_index(drop=True) \
                  if not flicker_ends.empty else None
        rows_a  = analysis_starts[analysis_starts["freq_hz"] == freq].reset_index(drop=True) \
                  if analysis_starts is not None and not analysis_starts.empty else None

        all_psds = {ch: [] for ch in channels}
        nperseg  = int(rec.fs * 4)   # fixed so all PSDs are the same length
        f_axis   = None   # computed from first valid segment; reused for all

        for i, row in rows_s.iterrows():
            # Analysis window start: prefer logged ANALYSIS_START, else warmup_s offset
            if rows_a is not None and i < len(rows_a):
                t0 = rows_a.iloc[i]["wall_time"] - rec_start
            else:
                t0 = row["wall_time"] - rec_start + warmup_s

            # Analysis window end: prefer logged FLICKER_END, else 15s default
            t1 = row["wall_time"] - rec_start + 15.0
            if rows_e is not None and i < len(rows_e):
                t1 = rows_e.iloc[i]["wall_time"] - rec_start

            s0 = max(0, int(t0 * rec.fs))
            s1 = min(rec.n_samples, int(t1 * rec.fs))
            if s1 - s0 < nperseg:   # skip epochs too short for fixed nperseg
                continue

            for ch in channels:
                ch_idx = rec.channel_index(ch)
                if ch_idx is None:
                    continue
                seg = rec.data[ch_idx][s0:s1]
                f_seg, psd = welch(seg, fs=rec.fs, nperseg=nperseg)
                all_psds[ch].append(psd)
                if f_axis is None:
                    f_axis = f_seg

        if f_axis is None:
            ax.set_title(f"{freq:.0f} Hz — no valid epochs")
            continue

        mask40 = f_axis <= 40
        snr_lines = []

        for ch in channels:
            if not all_psds[ch]:
                continue
            color  = CHANNEL_COLORS.get(ch, "#888")
            mean_p = np.mean(all_psds[ch], axis=0)
            ax.semilogy(f_axis[mask40], mean_p[mask40], color=color,
                        linewidth=1.2, label=ch)
            # Accumulate SNR at fundamental for subtitle
            s = _snr(mean_p, f_axis, freq, snr_bw_hz)
            if not np.isnan(s):
                snr_lines.append(f"{ch}: {s:.1f}×")

        # Mark target frequency and harmonics with correctly-positioned labels
        # Use axes-fraction coordinates (x=data, y=axes fraction) to avoid ylim issues
        trans = ax.get_xaxis_transform()
        for h in [1, 2, 3]:
            hf = freq * h
            if hf <= 40:
                ax.axvline(hf, color="#e53935", linewidth=0.8, linestyle="--", alpha=0.7)
                ax.text(hf, 0.97, f"{hf:.0f} Hz", transform=trans,
                        ha="center", va="top", fontsize=7, color="#e53935")

        snr_str = "  SNR: " + ", ".join(snr_lines) if snr_lines else ""
        ax.set_xlabel("Frequency (Hz)")
        ax.set_title(f"{freq:.0f} Hz stimulus\n(n={len(rows_s)} epochs){snr_str}",
                     fontsize=9)
        ax.legend(fontsize=7)

    axes[0].set_ylabel("PSD (uV²/Hz)")
    fig.tight_layout()
    return fig


def plot_eog_trace(rec: Recording,
                   t_start: float, t_end: float,
                   threshold_uv: float = 50.0) -> plt.Figure:
    """EOG channel over time with threshold lines and detected saccade markers."""
    from scipy.signal import butter, sosfilt
    idx = rec.channel_index("EOG")
    if idx is None:
        raise ValueError("No 'EOG' channel in this recording")

    s0 = int(t_start * rec.fs)
    s1 = int(t_end   * rec.fs)
    raw = rec.data[idx][s0:s1].copy()

    # HP filter to remove drift
    sos = butter(2, 0.5 / (rec.fs / 2), btype="high", output="sos")
    sig = sosfilt(sos, raw)
    t   = np.arange(s0, s1) / rec.fs

    # Simple threshold saccade detection for overlay markers
    above =  sig > threshold_uv
    below =  sig < -threshold_uv
    r_onsets = np.where(np.diff(above.astype(int)) > 0)[0]
    l_onsets = np.where(np.diff(below.astype(int)) > 0)[0]

    fig, ax = plt.subplots(figsize=(12, 3.5))
    ax.plot(t, sig, color=CHANNEL_COLORS["EOG"], linewidth=0.7, label="EOG (HP 0.5 Hz)")
    ax.axhline( threshold_uv, color="#e53935", linewidth=0.8, linestyle="--",
                label=f"+{threshold_uv:.0f} uV threshold")
    ax.axhline(-threshold_uv, color="#1565c0", linewidth=0.8, linestyle="--",
                label=f"−{threshold_uv:.0f} uV threshold")
    ax.scatter(t[r_onsets], sig[r_onsets], color="#e53935", s=25, zorder=5,
               label=f"Right saccades (n={len(r_onsets)})")
    ax.scatter(t[l_onsets], sig[l_onsets], color="#1565c0", s=25, zorder=5,
               label=f"Left saccades  (n={len(l_onsets)})")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("EOG (uV)")
    ax.set_title("EOG trace — horizontal saccade detection", fontweight="bold")
    ax.legend(fontsize=8, ncol=3)
    ax.set_xlim(t[0], t[-1])
    fig.tight_layout()
    return fig


# ── GUI ───────────────────────────────────────────────────────────────────────

class EDFViewer(tk.Tk):
    def __init__(self, initial_edf: str | None = None):
        super().__init__()
        self.title("EDF Viewer — EEG Wearable")
        self.configure(bg="#1e1e2e")
        self.geometry("940x720")
        self.resizable(True, True)

        self._rec: Recording | None = None
        self._events_df             = None
        self._figures: list[plt.Figure] = []

        self._build_ui()
        self._style_widgets()

        if initial_edf:
            self._load_edf(initial_edf)

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        PAD = dict(padx=6, pady=4)

        # ── Top: file pickers ─────────────────────────────────────────────────
        top = tk.Frame(self, bg="#1e1e2e")
        top.pack(fill="x", padx=10, pady=(10, 2))

        tk.Label(top, text="EDF:", bg="#1e1e2e", fg="#cdd6f4",
                 font=("monospace", 9)).grid(row=0, column=0, sticky="w", **PAD)
        self._edf_var = tk.StringVar()
        tk.Entry(top, textvariable=self._edf_var, width=52,
                 bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4",
                 relief="flat").grid(row=0, column=1, sticky="ew", **PAD)
        tk.Button(top, text="Browse", command=self._browse_edf,
                  bg="#45475a", fg="#cdd6f4", relief="flat",
                  activebackground="#585b70").grid(row=0, column=2, **PAD)
        tk.Button(top, text="Latest", command=self._load_latest,
                  bg="#45475a", fg="#cdd6f4", relief="flat",
                  activebackground="#585b70").grid(row=0, column=3, **PAD)

        tk.Label(top, text="Events:", bg="#1e1e2e", fg="#cdd6f4",
                 font=("monospace", 9)).grid(row=1, column=0, sticky="w", **PAD)
        self._events_var = tk.StringVar()
        tk.Entry(top, textvariable=self._events_var, width=52,
                 bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4",
                 relief="flat").grid(row=1, column=1, sticky="ew", **PAD)
        tk.Button(top, text="Browse", command=self._browse_events,
                  bg="#45475a", fg="#cdd6f4", relief="flat",
                  activebackground="#585b70").grid(row=1, column=2, **PAD)
        tk.Button(top, text="Latest", command=self._load_latest_events,
                  bg="#45475a", fg="#cdd6f4", relief="flat",
                  activebackground="#585b70").grid(row=1, column=3, **PAD)
        tk.Button(top, text="Load", command=self._do_load,
                  bg="#89b4fa", fg="#1e1e2e", relief="flat", font=("monospace", 9, "bold"),
                  activebackground="#74c7ec").grid(row=1, column=4, **PAD)
        top.columnconfigure(1, weight=1)

        # ── Info bar ──────────────────────────────────────────────────────────
        self._info_var = tk.StringVar(value="No file loaded")
        tk.Label(self, textvariable=self._info_var, bg="#181825", fg="#a6adc8",
                 font=("monospace", 8), anchor="w").pack(fill="x", padx=10)

        # ── Main body ─────────────────────────────────────────────────────────
        body = tk.Frame(self, bg="#1e1e2e")
        body.pack(fill="both", expand=True, padx=10, pady=4)
        body.columnconfigure(0, weight=0, minsize=180)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # ── Left panel ────────────────────────────────────────────────────────
        left = tk.Frame(body, bg="#181825", relief="flat", bd=1)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        def _lbl(parent, text):
            tk.Label(parent, text=text, bg="#181825", fg="#89b4fa",
                     font=("monospace", 8, "bold"), anchor="w").pack(
                         fill="x", padx=6, pady=(8, 2))

        # Channels
        _lbl(left, "CHANNELS")
        ch_frame = tk.Frame(left, bg="#181825")
        ch_frame.pack(fill="x", padx=6)
        self._ch_vars: dict[str, tk.BooleanVar] = {}
        self._ch_labels = ["EOG","EMG_far","EMG_near","EEG_L1","EEG_L2","EEG_L3","SRB1","DRL"]
        for ch in self._ch_labels:
            v = tk.BooleanVar(value=ch.startswith("EEG") or ch == "EOG")
            self._ch_vars[ch] = v
            c = CHANNEL_COLORS.get(ch, "#888")
            tk.Checkbutton(ch_frame, text=ch, variable=v,
                           bg="#181825", fg=c, selectcolor="#313244",
                           activebackground="#181825", relief="flat",
                           font=("monospace", 8)).pack(anchor="w")

        # Custom derivations
        _lbl(left, "CUSTOM DERIVATIONS")
        tk.Label(left, text="e.g.  EEG_L1 - EEG_L3\n      label : EEG_L1 - EEG_L3",
                 bg="#181825", fg="#6c7086",
                 font=("monospace", 7), justify="left").pack(anchor="w", padx=6)
        self._derived_text = tk.Text(
            left, height=3, width=22, bg="#313244", fg="#cdd6f4",
            insertbackground="#cdd6f4", relief="flat", font=("monospace", 8),
            wrap="none")
        self._derived_text.pack(fill="x", padx=6, pady=(2, 4))

        # Time range
        _lbl(left, "TIME RANGE (s)")
        tr = tk.Frame(left, bg="#181825")
        tr.pack(fill="x", padx=6)
        tk.Label(tr, text="Start", bg="#181825", fg="#a6adc8",
                 font=("monospace", 8)).grid(row=0, column=0, sticky="w")
        self._t_start = tk.DoubleVar(value=0.0)
        tk.Entry(tr, textvariable=self._t_start, width=7,
                 bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4",
                 relief="flat").grid(row=0, column=1, padx=4)
        tk.Label(tr, text="End", bg="#181825", fg="#a6adc8",
                 font=("monospace", 8)).grid(row=1, column=0, sticky="w")
        self._t_end = tk.DoubleVar(value=30.0)
        tk.Entry(tr, textvariable=self._t_end, width=7,
                 bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4",
                 relief="flat").grid(row=1, column=1, padx=4)
        tk.Button(tr, text="Full recording", command=self._set_full_range,
                  bg="#45475a", fg="#cdd6f4", relief="flat",
                  font=("monospace", 7)).grid(row=2, column=0, columnspan=2,
                                              sticky="ew", pady=(4, 0))

        # Filters
        _lbl(left, "FILTERS")
        fl = tk.Frame(left, bg="#181825")
        fl.pack(fill="x", padx=6)
        tk.Label(fl, text="HP (Hz)", bg="#181825", fg="#a6adc8",
                 font=("monospace", 8)).grid(row=0, column=0, sticky="w")
        self._hp_var = tk.StringVar(value="0.5")
        tk.Entry(fl, textvariable=self._hp_var, width=6,
                 bg="#313244", fg="#cdd6f4", relief="flat").grid(row=0, column=1, padx=4)
        tk.Label(fl, text="LP (Hz)", bg="#181825", fg="#a6adc8",
                 font=("monospace", 8)).grid(row=1, column=0, sticky="w")
        self._lp_var = tk.StringVar(value="40")
        tk.Entry(fl, textvariable=self._lp_var, width=6,
                 bg="#313244", fg="#cdd6f4", relief="flat").grid(row=1, column=1, padx=4)
        self._notch_var = tk.BooleanVar(value=True)
        tk.Checkbutton(fl, text="60 Hz notch", variable=self._notch_var,
                       bg="#181825", fg="#a6adc8", selectcolor="#313244",
                       activebackground="#181825", relief="flat",
                       font=("monospace", 8)).grid(row=2, column=0,
                                                    columnspan=2, sticky="w")

        # EOG threshold
        _lbl(left, "EOG THRESHOLD (uV)")
        self._eog_thr = tk.DoubleVar(value=50.0)
        tk.Entry(left, textvariable=self._eog_thr, width=8,
                 bg="#313244", fg="#cdd6f4", relief="flat").pack(
                     anchor="w", padx=6)

        # Output
        _lbl(left, "OUTPUT")
        out_f = tk.Frame(left, bg="#181825")
        out_f.pack(fill="x", padx=6)
        self._fmt_var = tk.StringVar(value="png")
        for fmt in ("png", "svg", "pdf"):
            tk.Radiobutton(out_f, text=fmt.upper(), variable=self._fmt_var, value=fmt,
                           bg="#181825", fg="#a6adc8", selectcolor="#313244",
                           activebackground="#181825", relief="flat",
                           font=("monospace", 8)).pack(side="left")

        # ── Right panel ───────────────────────────────────────────────────────
        right = tk.Frame(body, bg="#181825", relief="flat", bd=1)
        right.grid(row=0, column=1, sticky="nsew")

        def _section(parent, text):
            tk.Label(parent, text=text, bg="#181825", fg="#89b4fa",
                     font=("monospace", 8, "bold"), anchor="w").pack(
                         fill="x", padx=10, pady=(10, 2))

        _section(right, "TIME DOMAIN")
        self._plot_raw    = tk.BooleanVar(value=True)
        self._plot_filt   = tk.BooleanVar(value=True)
        tk.Checkbutton(right, text="Raw traces (unfiltered)",
                       variable=self._plot_raw,
                       bg="#181825", fg="#cdd6f4", selectcolor="#313244",
                       activebackground="#181825", relief="flat",
                       font=("monospace", 9)).pack(anchor="w", padx=18)
        tk.Checkbutton(right, text="Filtered traces",
                       variable=self._plot_filt,
                       bg="#181825", fg="#cdd6f4", selectcolor="#313244",
                       activebackground="#181825", relief="flat",
                       font=("monospace", 9)).pack(anchor="w", padx=18)

        _section(right, "FREQUENCY DOMAIN")
        self._plot_psd_per  = tk.BooleanVar(value=True)
        self._plot_psd_avg  = tk.BooleanVar(value=False)
        self._plot_specgram = tk.BooleanVar(value=False)
        tk.Checkbutton(right, text="PSD per channel",
                       variable=self._plot_psd_per,
                       bg="#181825", fg="#cdd6f4", selectcolor="#313244",
                       activebackground="#181825", relief="flat",
                       font=("monospace", 9)).pack(anchor="w", padx=18)
        tk.Checkbutton(right, text="PSD averaged",
                       variable=self._plot_psd_avg,
                       bg="#181825", fg="#cdd6f4", selectcolor="#313244",
                       activebackground="#181825", relief="flat",
                       font=("monospace", 9)).pack(anchor="w", padx=18)
        # Spectrogram — channel picker
        sg_row = tk.Frame(right, bg="#181825")
        sg_row.pack(anchor="w", padx=18)
        tk.Checkbutton(sg_row, text="Spectrogram — channel:",
                       variable=self._plot_specgram,
                       bg="#181825", fg="#cdd6f4", selectcolor="#313244",
                       activebackground="#181825", relief="flat",
                       font=("monospace", 9)).pack(side="left")
        self._sg_ch_var = tk.StringVar(value="EEG_L1")
        ttk.Combobox(sg_row, textvariable=self._sg_ch_var,
                     values=self._ch_labels, width=10,
                     state="readonly").pack(side="left", padx=4)

        _section(right, "ERP / BCI  (requires events CSV)")
        self._plot_erp      = tk.BooleanVar(value=False)
        self._plot_ssvep    = tk.BooleanVar(value=False)
        self._plot_ssvep_snr = tk.BooleanVar(value=False)
        tk.Checkbutton(right, text="ERP — P300  (auditory oddball)",
                       variable=self._plot_erp,
                       bg="#181825", fg="#a6e3a1", selectcolor="#313244",
                       activebackground="#181825", relief="flat",
                       font=("monospace", 9)).pack(anchor="w", padx=18)
        tk.Checkbutton(right, text="SSVEP spectrum  (PSD per freq)",
                       variable=self._plot_ssvep,
                       bg="#181825", fg="#a6e3a1", selectcolor="#313244",
                       activebackground="#181825", relief="flat",
                       font=("monospace", 9)).pack(anchor="w", padx=18)
        tk.Checkbutton(right, text="SSVEP SNR bar chart  (1f/2f/3f)",
                       variable=self._plot_ssvep_snr,
                       bg="#181825", fg="#a6e3a1", selectcolor="#313244",
                       activebackground="#181825", relief="flat",
                       font=("monospace", 9)).pack(anchor="w", padx=18)

        _section(right, "EOG")
        self._plot_eog = tk.BooleanVar(value=False)
        tk.Checkbutton(right, text="EOG trace + saccade markers",
                       variable=self._plot_eog,
                       bg="#181825", fg="#89dceb", selectcolor="#313244",
                       activebackground="#181825", relief="flat",
                       font=("monospace", 9)).pack(anchor="w", padx=18)

        # ── Generate button + status ──────────────────────────────────────────
        bot = tk.Frame(self, bg="#1e1e2e")
        bot.pack(fill="x", padx=10, pady=(4, 10))
        self._gen_btn = tk.Button(bot, text="Generate Plots",
                                  command=self._generate,
                                  bg="#a6e3a1", fg="#1e1e2e", relief="flat",
                                  font=("monospace", 10, "bold"),
                                  activebackground="#94e2d5")
        self._gen_btn.pack(side="left", ipadx=12, ipady=4)
        self._status_var = tk.StringVar(value="")
        tk.Label(bot, textvariable=self._status_var, bg="#1e1e2e", fg="#a6adc8",
                 font=("monospace", 8)).pack(side="left", padx=12)

    def _style_widgets(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground="#313244",
                        background="#313244", foreground="#cdd6f4")

    # ── File I/O ──────────────────────────────────────────────────────────────

    def _browse_edf(self):
        p = filedialog.askopenfilename(
            initialdir=RECORDINGS_DIR,
            filetypes=[("EDF files", "*.edf"), ("All files", "*.*")])
        if p:
            self._edf_var.set(p)

    def _browse_events(self):
        p = filedialog.askopenfilename(
            initialdir=EVENTS_DIR,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if p:
            self._events_var.set(p)

    def _load_latest(self):
        files = sorted(glob.glob(os.path.join(RECORDINGS_DIR, "*.edf")),
                       key=os.path.getmtime, reverse=True)
        if not files:
            messagebox.showwarning("No EDFs", f"No EDF files found in {RECORDINGS_DIR}")
            return
        self._edf_var.set(files[0])
        self._do_load()
        # Auto-find the closest events file by modification time
        self._auto_match_events(files[0])

    def _load_latest_events(self):
        """Load the most recently modified events CSV from the events directory."""
        files = sorted(glob.glob(os.path.join(EVENTS_DIR, "*.csv")),
                       key=os.path.getmtime, reverse=True)
        if not files:
            messagebox.showwarning("No events", f"No CSV files found in {EVENTS_DIR}")
            return
        self._events_var.set(files[0])
        self._load_events(files[0])

    def _auto_match_events(self, edf_path: str):
        """Try to find an events CSV whose timestamp is closest to the EDF's mtime."""
        ev_files = sorted(glob.glob(os.path.join(EVENTS_DIR, "*.csv")),
                          key=os.path.getmtime, reverse=True)
        if not ev_files:
            return
        edf_mtime = os.path.getmtime(edf_path)
        # Pick the events file whose mtime is closest to the EDF mtime
        best = min(ev_files, key=lambda p: abs(os.path.getmtime(p) - edf_mtime))
        delta = abs(os.path.getmtime(best) - edf_mtime)
        # Only auto-load if within 30 minutes — avoids matching unrelated sessions
        if delta <= 1800:
            self._events_var.set(best)
            self._load_events(best)
            self._status(f"Auto-matched events: {os.path.basename(best)}")

    def _do_load(self):
        edf_path = self._edf_var.get().strip()
        if not edf_path or not os.path.exists(edf_path):
            messagebox.showerror("Error", "EDF file not found")
            return
        self._load_edf(edf_path)
        ev_path = self._events_var.get().strip()
        if ev_path and os.path.exists(ev_path):
            self._load_events(ev_path)

    def _load_edf(self, path: str):
        self._status("Loading EDF...")
        try:
            self._rec = Recording(path)
        except Exception as e:
            messagebox.showerror("Load error", str(e))
            return
        dur = self._rec.duration_s
        self._t_end.set(round(min(30.0, dur), 1))
        sync = (f"  rec_start={datetime.fromtimestamp(self._rec.rec_start_epoch).strftime('%H:%M:%S')}"
                if self._rec.rec_start_epoch else "  (no meta.json)")
        self._info_var.set(
            f"{os.path.basename(path)}  |  "
            f"fs={self._rec.fs} Hz  |  "
            f"dur={dur:.1f}s  |  "
            f"ch={self._rec.labels}{sync}")
        self._status(f"Loaded {os.path.basename(path)}")

    def _load_events(self, path: str):
        import pandas as pd
        try:
            self._events_df = pd.read_csv(path)
            n = len(self._events_df)
            self._status(f"Events loaded: {n} rows from {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Events load error", str(e))

    def _set_full_range(self):
        if self._rec:
            self._t_start.set(0.0)
            self._t_end.set(round(self._rec.duration_s, 1))

    # ── Plot generation ───────────────────────────────────────────────────────

    def _get_filters(self):
        try:
            hp = float(self._hp_var.get()) if self._hp_var.get().strip() else None
        except ValueError:
            hp = None
        try:
            lp = float(self._lp_var.get()) if self._lp_var.get().strip() else None
        except ValueError:
            lp = None
        return hp, lp, self._notch_var.get()

    def _generate(self):
        if not self._rec:
            messagebox.showwarning("No data", "Load an EDF file first")
            return
        self._gen_btn.config(state="disabled")
        threading.Thread(target=self._generate_worker, daemon=True).start()

    def _generate_worker(self):
        try:
            self._do_generate()
        except Exception as e:
            self.after(0, lambda err=e: messagebox.showerror("Plot error", str(err)))
        finally:
            self.after(0, lambda: self._gen_btn.config(state="normal"))

    def _do_generate(self):
        import pandas as pd

        rec     = self._rec
        t_start = self._t_start.get()
        t_end   = min(self._t_end.get(), rec.duration_s)
        hp, lp, notch = self._get_filters()
        channels = [ch for ch, v in self._ch_vars.items() if v.get()
                    and rec.channel_index(ch) is not None]
        fmt      = self._fmt_var.get()

        derived_exprs = self._derived_text.get("1.0", tk.END).strip()
        if not channels and not derived_exprs:
            self.after(0, lambda: messagebox.showwarning("No channels", "Select at least one channel or enter a custom derivation"))
            return

        # Parse custom derivations (raw arrays; filters applied inside plot functions)
        extras: list[tuple[str, np.ndarray]] = []
        if derived_exprs:
            try:
                extras = _parse_derived_channels(rec, derived_exprs, t_start, t_end)
            except ValueError as e:
                self.after(0, lambda err=e: messagebox.showerror("Derivation error", str(err)))
                return

        os.makedirs(FIGURES_DIR, exist_ok=True)
        stem = os.path.splitext(os.path.basename(rec.path))[0]
        saved = []

        def _save(fig: plt.Figure, suffix: str):
            path = os.path.join(FIGURES_DIR, f"{stem}_{suffix}.{fmt}")
            fig.savefig(path, bbox_inches="tight")
            plt.close(fig)
            saved.append(path)

        self._status("Plotting...")

        if self._plot_raw.get():
            fig = plot_traces(rec, channels, t_start, t_end,
                              None, None, False, " — raw", extras=extras)
            _save(fig, "raw_traces")
            self._status(f"Saved raw traces ({len(saved)} so far)")

        if self._plot_filt.get():
            fig = plot_traces(rec, channels, t_start, t_end,
                              hp, lp, notch, " — filtered", extras=extras)
            _save(fig, "filtered_traces")
            self._status(f"Saved filtered traces ({len(saved)} so far)")

        if self._plot_psd_per.get():
            fig = plot_psd(rec, channels, t_start, t_end, hp, lp, notch,
                           per_channel=True, extras=extras)
            _save(fig, "psd_per_channel")
            self._status(f"Saved PSD per channel ({len(saved)} so far)")

        if self._plot_psd_avg.get():
            fig = plot_psd(rec, channels, t_start, t_end, hp, lp, notch,
                           per_channel=False, extras=extras)
            _save(fig, "psd_averaged")
            self._status(f"Saved PSD averaged ({len(saved)} so far)")

        if self._plot_specgram.get():
            sg_ch = self._sg_ch_var.get()
            try:
                fig = plot_spectrogram_fig(rec, sg_ch, t_start, t_end, hp, lp, notch)
                _save(fig, f"spectrogram_{sg_ch}")
                self._status(f"Saved spectrogram ({len(saved)} so far)")
            except ValueError as e:
                self.after(0, lambda: messagebox.showwarning("Spectrogram", str(e)))

        eeg_channels = [ch for ch in channels if ch.startswith("EEG")]

        if self._plot_erp.get():
            if self._events_df is None:
                self.after(0, lambda: messagebox.showwarning(
                    "ERP", "Load an events CSV first (auditory_oddball events)"))
            elif rec.rec_start_epoch is None:
                self.after(0, lambda: messagebox.showwarning(
                    "ERP", "No meta.json found — rec_start_epoch unknown.\n"
                           "Save the EDF from eeg_stream_pg.py (not an old recording)."))
            elif not eeg_channels:
                self.after(0, lambda: messagebox.showwarning("ERP", "Select EEG channels"))
            else:
                try:
                    fig = plot_erp(rec, eeg_channels, self._events_df,
                                   rec.rec_start_epoch)
                    _save(fig, "ERP_P300")
                    self._status(f"Saved ERP ({len(saved)} so far)")
                except Exception as e:
                    self.after(0, lambda err=e: messagebox.showerror("ERP error", str(err)))

        def _ssvep_preflight(label):
            if self._events_df is None:
                self.after(0, lambda: messagebox.showwarning(
                    label, "Load an events CSV first (ssvep events)"))
                return False
            if rec.rec_start_epoch is None:
                self.after(0, lambda: messagebox.showwarning(
                    label, "No meta.json — rec_start_epoch unknown"))
                return False
            if not eeg_channels:
                self.after(0, lambda: messagebox.showwarning(label, "Select EEG channels"))
                return False
            return True

        if self._plot_ssvep.get():
            if _ssvep_preflight("SSVEP"):
                try:
                    fig = plot_ssvep(rec, eeg_channels, self._events_df,
                                     rec.rec_start_epoch)
                    _save(fig, "SSVEP_spectrum")
                    self._status(f"Saved SSVEP spectrum ({len(saved)} so far)")
                except Exception as e:
                    self.after(0, lambda err=e: messagebox.showerror("SSVEP error", str(err)))

        if self._plot_ssvep_snr.get():
            if _ssvep_preflight("SSVEP SNR"):
                try:
                    fig = plot_ssvep_snr(rec, eeg_channels, self._events_df,
                                         rec.rec_start_epoch)
                    _save(fig, "SSVEP_SNR")
                    self._status(f"Saved SSVEP SNR ({len(saved)} so far)")
                except Exception as e:
                    self.after(0, lambda err=e: messagebox.showerror("SSVEP SNR error", str(err)))

        if self._plot_eog.get():
            try:
                fig = plot_eog_trace(rec, t_start, t_end,
                                     threshold_uv=self._eog_thr.get())
                _save(fig, "EOG_saccades")
                self._status(f"Saved EOG trace ({len(saved)} so far)")
            except ValueError as e:
                self.after(0, lambda err=e: messagebox.showwarning("EOG", str(err)))

        msg = f"Done — {len(saved)} figure(s) saved to {FIGURES_DIR}"
        self._status(msg)
        self.after(0, lambda: messagebox.showinfo("Done", msg))

    def _status(self, msg: str):
        self.after(0, lambda: self._status_var.set(msg))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EDF Viewer — EEG Wearable")
    parser.add_argument("--edf", help="EDF file to load on startup")
    args = parser.parse_args()

    app = EDFViewer(initial_edf=args.edf)
    app.mainloop()
