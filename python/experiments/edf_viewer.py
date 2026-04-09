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
from scipy.signal import butter, iirnotch, sosfilt, tf2sos, welch, spectrogram

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
            self.labels  = list(f.getSignalLabels())
            n            = f.signals_in_file
            fs_vals      = [int(f.getSampleFrequency(i)) for i in range(n)]
            self.fs      = fs_vals[0]
            self.data    = np.array([f.readSignal(i) for i in range(n)],
                                    dtype=np.float64)   # (n_ch, n_samples)
            self.start_dt = f.getStartdatetime()        # datetime or None
        finally:
            f.close()
        self.path         = edf_path
        self.n_samples    = self.data.shape[1]
        self.duration_s   = self.n_samples / self.fs
        # Try to load matching meta.json for rec_start_epoch
        self.rec_start_epoch = self._load_rec_start()

    def _load_rec_start(self) -> float | None:
        stem     = os.path.splitext(os.path.basename(self.path))[0]
        meta_path = os.path.join(os.path.dirname(self.path), f"{stem}_meta.json")
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                return json.load(f)["rec_start_epoch"]
        return None

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
                rows.append(self.data[idx, s0:s1])
        return np.array(rows), t


# ── Signal processing helpers ─────────────────────────────────────────────────

def _hp_filter(data: np.ndarray, fs: int, cutoff: float) -> np.ndarray:
    sos = butter(2, cutoff / (fs / 2), btype="high", output="sos")
    return sosfilt(sos, data, axis=-1)

def _lp_filter(data: np.ndarray, fs: int, cutoff: float) -> np.ndarray:
    sos = butter(4, cutoff / (fs / 2), btype="low", output="sos")
    return sosfilt(sos, data, axis=-1)

def _notch_filter(data: np.ndarray, fs: int, freq: float = 60.0) -> np.ndarray:
    b, a = iirnotch(freq, Q=30, fs=fs)
    sos  = tf2sos(b, a)
    return sosfilt(sos, data, axis=-1)

def _apply_filters(data: np.ndarray, fs: int,
                   hp: float | None, lp: float | None,
                   notch: bool) -> np.ndarray:
    out = data.copy()
    if hp:
        out = _hp_filter(out, fs, hp)
    if lp:
        out = _lp_filter(out, fs, lp)
    if notch:
        out = _notch_filter(out, fs)
    return out


# ── Plot functions ─────────────────────────────────────────────────────────────

def plot_traces(rec: Recording, channels: list[str],
                t_start: float, t_end: float,
                hp: float | None, lp: float | None, notch: bool,
                title_suffix: str = "") -> plt.Figure:
    """Time-domain traces for selected channels and window."""
    data, t = rec.time_slice(channels, t_start, t_end)
    data = _apply_filters(data, rec.fs, hp, lp, notch)

    n_ch = len(channels)
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

    for ax, ch, row in zip(axes, channels, data):
        color = CHANNEL_COLORS.get(ch, "#888888")
        ax.plot(t, row, color=color, linewidth=0.7)
        ax.set_ylabel(f"{ch}\n(µV)", fontsize=9)
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
             per_channel: bool = True) -> plt.Figure:
    """Welch power spectral density."""
    data, _ = rec.time_slice(channels, t_start, t_end)
    data = _apply_filters(data, rec.fs, hp, lp, notch)

    nperseg = min(rec.fs * 4, data.shape[1])
    fig, ax = plt.subplots(figsize=(9, 5))

    for ch, row in zip(channels, data):
        f, psd = welch(row, fs=rec.fs, nperseg=nperseg)
        color  = CHANNEL_COLORS.get(ch, "#888888")
        if per_channel:
            ax.semilogy(f, psd, color=color, label=ch)
        else:
            ax.semilogy(f, psd, color=color, alpha=0.3, linewidth=0.8)

    if not per_channel and len(data) > 0:
        # Compute and plot average PSD
        psds = [welch(row, fs=rec.fs, nperseg=nperseg)[1] for row in data]
        f, _ = welch(data[0], fs=rec.fs, nperseg=nperseg)
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
    ax.set_ylabel("PSD (µV²/Hz)")
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
    raw = rec.data[idx, s0:s1]
    raw = _apply_filters(raw[np.newaxis, :], rec.fs, hp, lp, notch)[0]

    nperseg = min(rec.fs, len(raw) // 4)
    f, t_sg, Sxx = spectrogram(raw, fs=rec.fs, nperseg=nperseg,
                                noverlap=nperseg // 2)
    t_sg += t_start  # align to recording time

    fig, (ax_sig, ax_sg) = plt.subplots(2, 1, figsize=(12, 6),
                                         gridspec_kw={"height_ratios": [1, 2.5]})
    t_full = np.arange(s0, s1) / rec.fs
    ax_sig.plot(t_full, raw, color=CHANNEL_COLORS.get(channel, "#888"), linewidth=0.6)
    ax_sig.set_ylabel("µV")
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
             tmin: float = -0.2, tmax: float = 0.8) -> plt.Figure:
    """
    P300 ERP: epoch around deviant onsets, average with SEM band.
    events_df must have columns: wall_time, event  (values: 'deviant', 'standard').
    rec_start: time.time() epoch when EEG recording started.
    """
    import pandas as pd

    deviant_times  = events_df.loc[events_df["event"] == "deviant",  "wall_time"].values
    standard_times = events_df.loc[events_df["event"] == "standard", "wall_time"].values

    fs = rec.fs
    n_pre  = int(abs(tmin) * fs)
    n_post = int(tmax * fs)
    n_epoch = n_pre + n_post
    t_ep = np.linspace(tmin, tmax, n_epoch)

    def _epoch(onset_times):
        epochs = []
        for wt in onset_times:
            s0 = int((wt - rec_start) * fs) - n_pre
            s1 = s0 + n_epoch
            if s0 < 0 or s1 > rec.n_samples:
                continue
            rows = []
            for ch in channels:
                idx = rec.channel_index(ch)
                if idx is not None:
                    rows.append(rec.data[idx, s0:s1])
            if rows:
                ep = np.array(rows)
                # Baseline correct to pre-stimulus window
                bl = ep[:, :n_pre].mean(axis=1, keepdims=True)
                epochs.append(ep - bl)
        return np.array(epochs) if epochs else None   # (n_epochs, n_ch, n_samples)

    dev_ep  = _epoch(deviant_times)
    std_ep  = _epoch(standard_times)

    n_ch = len(channels)
    fig, axes = plt.subplots(1, n_ch, figsize=(4.5 * n_ch, 4.5), sharey=True)
    if n_ch == 1:
        axes = [axes]

    fig.suptitle("ERP — P300 (deviant vs standard)", fontweight="bold")

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

    axes[0].set_ylabel("Amplitude (µV, baseline corrected)")
    fig.tight_layout()
    return fig


def plot_ssvep(rec: Recording, channels: list[str],
               events_df, rec_start: float) -> plt.Figure:
    """
    SSVEP: FFT of flicker epochs per target frequency.
    events_df must have columns: wall_time, freq_hz, event.
    """
    flicker_starts = events_df[events_df["event"] == "FLICKER_START"].copy()
    flicker_ends   = events_df[events_df["event"] == "FLICKER_END"].copy()

    if flicker_starts.empty:
        raise ValueError("No FLICKER_START events found in event log")

    target_freqs = sorted(flicker_starts["freq_hz"].dropna().unique())
    n_freqs = len(target_freqs)
    fig, axes = plt.subplots(1, n_freqs, figsize=(4.5 * n_freqs, 4.5), sharey=True)
    if n_freqs == 1:
        axes = [axes]

    fig.suptitle("SSVEP — power spectrum during flicker epochs", fontweight="bold")

    for ax, freq in zip(axes, target_freqs):
        rows_s = flicker_starts[flicker_starts["freq_hz"] == freq]
        rows_e = flicker_ends[flicker_ends["freq_hz"] == freq] if not flicker_ends.empty else None

        all_psds = {ch: [] for ch in channels}

        for i, (_, row) in enumerate(rows_s.iterrows()):
            t0 = row["wall_time"] - rec_start
            # Try to get actual end time; fallback to 12 s epoch
            t1 = t0 + 12.0
            if rows_e is not None and len(rows_e) > i:
                t1 = rows_e.iloc[i]["wall_time"] - rec_start

            s0 = max(0, int(t0 * rec.fs))
            s1 = min(rec.n_samples, int(t1 * rec.fs))
            if s1 - s0 < rec.fs:
                continue

            for ch in channels:
                idx = rec.channel_index(ch)
                if idx is not None:
                    seg  = rec.data[idx, s0:s1]
                    nperseg = min(rec.fs * 4, len(seg))
                    f, psd = welch(seg, fs=rec.fs, nperseg=nperseg)
                    all_psds[ch].append(psd)

        for ch in channels:
            if not all_psds[ch]:
                continue
            color  = CHANNEL_COLORS.get(ch, "#888")
            mean_p = np.mean(all_psds[ch], axis=0)
            ax.semilogy(f[f <= 40], mean_p[f <= 40], color=color,
                        linewidth=1.2, label=ch)

        # Mark target frequency and harmonics
        for h in [1, 2, 3]:
            hf = freq * h
            if hf <= 40:
                ax.axvline(hf, color="#e53935", linewidth=0.8, linestyle="--", alpha=0.7)
                ax.text(hf, ax.get_ylim()[1] if ax.get_ylim()[1] > 0 else 1,
                        f"{hf:.0f}", ha="center", va="bottom",
                        fontsize=8, color="#e53935")

        ax.set_xlabel("Frequency (Hz)")
        ax.set_title(f"{freq:.0f} Hz stimulus")
        ax.legend(fontsize=8)

    axes[0].set_ylabel("PSD (µV²/Hz)")
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
    raw = rec.data[idx, s0:s1].copy()

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
                label=f"+{threshold_uv:.0f} µV threshold")
    ax.axhline(-threshold_uv, color="#1565c0", linewidth=0.8, linestyle="--",
                label=f"−{threshold_uv:.0f} µV threshold")
    ax.scatter(t[r_onsets], sig[r_onsets], color="#e53935", s=25, zorder=5,
               label=f"Right saccades (n={len(r_onsets)})")
    ax.scatter(t[l_onsets], sig[l_onsets], color="#1565c0", s=25, zorder=5,
               label=f"Left saccades  (n={len(l_onsets)})")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("EOG (µV)")
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
        tk.Button(top, text="Load", command=self._do_load,
                  bg="#89b4fa", fg="#1e1e2e", relief="flat", font=("monospace", 9, "bold"),
                  activebackground="#74c7ec").grid(row=1, column=3, **PAD)
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
        _lbl(left, "EOG THRESHOLD (µV)")
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
        self._plot_erp   = tk.BooleanVar(value=False)
        self._plot_ssvep = tk.BooleanVar(value=False)
        tk.Checkbutton(right, text="ERP — P300  (auditory oddball)",
                       variable=self._plot_erp,
                       bg="#181825", fg="#a6e3a1", selectcolor="#313244",
                       activebackground="#181825", relief="flat",
                       font=("monospace", 9)).pack(anchor="w", padx=18)
        tk.Checkbutton(right, text="SSVEP spectrum",
                       variable=self._plot_ssvep,
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
            self.after(0, lambda: messagebox.showerror("Plot error", str(e)))
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

        if not channels:
            self.after(0, lambda: messagebox.showwarning("No channels", "Select at least one channel"))
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
                              None, None, False, " — raw")
            _save(fig, "raw_traces")
            self._status(f"Saved raw traces ({len(saved)} so far)")

        if self._plot_filt.get():
            fig = plot_traces(rec, channels, t_start, t_end,
                              hp, lp, notch, " — filtered")
            _save(fig, "filtered_traces")
            self._status(f"Saved filtered traces ({len(saved)} so far)")

        if self._plot_psd_per.get():
            fig = plot_psd(rec, channels, t_start, t_end, hp, lp, notch,
                           per_channel=True)
            _save(fig, "psd_per_channel")
            self._status(f"Saved PSD per channel ({len(saved)} so far)")

        if self._plot_psd_avg.get():
            fig = plot_psd(rec, channels, t_start, t_end, hp, lp, notch,
                           per_channel=False)
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
                    self.after(0, lambda: messagebox.showerror("ERP error", str(e)))

        if self._plot_ssvep.get():
            if self._events_df is None:
                self.after(0, lambda: messagebox.showwarning(
                    "SSVEP", "Load an events CSV first (ssvep events)"))
            elif rec.rec_start_epoch is None:
                self.after(0, lambda: messagebox.showwarning(
                    "SSVEP", "No meta.json — rec_start_epoch unknown"))
            elif not eeg_channels:
                self.after(0, lambda: messagebox.showwarning("SSVEP", "Select EEG channels"))
            else:
                try:
                    fig = plot_ssvep(rec, eeg_channels, self._events_df,
                                     rec.rec_start_epoch)
                    _save(fig, "SSVEP_spectrum")
                    self._status(f"Saved SSVEP ({len(saved)} so far)")
                except Exception as e:
                    self.after(0, lambda: messagebox.showerror("SSVEP error", str(e)))

        if self._plot_eog.get():
            try:
                fig = plot_eog_trace(rec, t_start, t_end,
                                     threshold_uv=self._eog_thr.get())
                _save(fig, "EOG_saccades")
                self._status(f"Saved EOG trace ({len(saved)} so far)")
            except ValueError as e:
                self.after(0, lambda: messagebox.showwarning("EOG", str(e)))

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
