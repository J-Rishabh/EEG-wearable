#!/usr/bin/env python3
"""
sleep_analysis_yasa.py — YASA-based overnight sleep analysis
=============================================================
Replaces the hand-tuned rule-based staging in sleep_analysis.py with
YASA (Yet Another Sleep Analyzer), a LightGBM classifier trained on
~3000 polysomnography recordings.

Requirements:
    pip install yasa mne

Usage:
    python experiments/sleep_analysis_yasa.py \\
        --edf recordings/eeg_20260412_034658_SLEEP.edf

    python experiments/sleep_analysis_yasa.py \\
        --edf recordings/eeg_20260412_034658_SLEEP.edf \\
        --fitbit experiments/fitbit/fitbit_20260411.json \\
        --age 22 --male

Outputs (saved to --out dir, default experiments/figures/):
    *_hypnogram.png       — hypnogram with per-epoch confidence
    *_spectrogram.png     — spectrogram + hypnogram overlay (YASA)
    *_spindles.png        — detected spindle waveform gallery
    *_slow_waves.png      — detected slow-wave gallery
    *_summary.png         — stage distribution + sleep stats
    *_comparison.png      — EEG vs Fitbit agreement (if --fitbit given)
"""

import os
import sys
import json
import argparse
import warnings
from datetime import datetime, timedelta
from typing import Optional, Tuple

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# ── Constants ──────────────────────────────────────────────────────────────────

EPOCH_S  = 30        # AASM standard
EEG_CH   = "EEG_L2"
EOG_CH   = "EOG"
EMG_CH   = "EMG_far"

# YASA → integer (matches PSG convention)
YASA_TO_INT = {"W": 0, "N1": 1, "N2": 2, "N3": 3, "R": 4}
INT_TO_LABEL = {0: "Wake", 1: "N1", 2: "N2", 3: "N3", 4: "REM"}
STAGE_COLORS = {"Wake": "#e74c3c", "N1": "#f39c12", "N2": "#3498db",
                "N3": "#1abc9c",   "REM": "#9b59b6"}

# Fitbit 4-stage → YASA int
FITBIT_MAP = {"Awake": 0, "Light": 2, "Deep": 3, "REM": 4}

# ── EDF loading ────────────────────────────────────────────────────────────────

def load_raw(edf_path: str):
    """Load EDF via MNE, set channel types, return Raw object."""
    import mne
    raw = mne.io.read_raw_edf(edf_path, preload=True, verbose=False)
    # Set types so YASA and MNE know what each channel is
    type_map = {}
    for ch in raw.ch_names:
        if ch.startswith("EEG"):
            type_map[ch] = "eeg"
        elif ch == EOG_CH:
            type_map[ch] = "eog"
        elif ch.startswith("EMG"):
            type_map[ch] = "emg"
        elif ch in ("ACCEL_X", "ACCEL_Y", "ACCEL_Z", "MOTION", "IMU_TEMP"):
            type_map[ch] = "misc"
    raw.set_channel_types(type_map, verbose=False)
    print(f"[LOAD] {os.path.basename(edf_path)}")
    print(f"       {raw.n_times/raw.info['sfreq']/3600:.2f} h  "
          f"({raw.n_times} samples @ {raw.info['sfreq']:.0f} Hz)")
    print(f"       Channels: {raw.ch_names}")
    return raw


def load_rec_start(edf_path: str) -> Optional[float]:
    stem = os.path.splitext(edf_path)[0]
    meta_path = stem + "_meta.json"
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            return json.load(f).get("rec_start_epoch")
    return None

# ── Fitbit parsing (mirrors sleep_analysis.py) ─────────────────────────────────

def parse_fitbit(gt: dict, rec_start_epoch: float,
                 n_epochs: int, epoch_s: float = 30.0
                 ) -> Tuple[Optional[np.ndarray], Optional[float], Optional[float]]:
    """
    Returns (fitbit_hypno, fitbit_start_epoch, fitbit_end_epoch).
    fitbit_start/end are wall-clock timestamps of the first/last Fitbit segment.
    """
    segs = gt.get("segments", [])
    if not segs:
        return None, None, None
    base_date = datetime.strptime(gt["date"], "%Y-%m-%d")
    parsed = []
    day_offset = timedelta(0)
    prev_end_dt = None
    for start_str, end_str, stage in segs:
        sh, sm = start_str.split(":")
        eh, em = end_str.split(":")
        start_dt = base_date + day_offset + timedelta(hours=int(sh), minutes=int(sm))
        end_dt   = base_date + day_offset + timedelta(hours=int(eh), minutes=int(em))
        if prev_end_dt is not None and start_dt < prev_end_dt:
            day_offset += timedelta(days=1)
            start_dt += timedelta(days=1)
            end_dt   += timedelta(days=1)
        if end_dt < start_dt:
            end_dt += timedelta(days=1)
        if end_dt == start_dt:
            # zero-duration segment (Fitbit artifact) — skip entirely so it
            # doesn't corrupt prev_end_dt and push subsequent segments to next day
            continue
        parsed.append((start_dt.timestamp(), end_dt.timestamp(),
                       FITBIT_MAP.get(stage, 0)))
        prev_end_dt = end_dt

    fitbit_start = parsed[0][0]
    fitbit_end   = parsed[-1][1]

    # Fitbit JSON date is often the evening date even when sleep crosses midnight,
    # so post-midnight segments (e.g. 3:52 AM) get parsed as the day before.
    # If Fitbit window is >12h before rec_start, shift everything forward 1 day.
    if rec_start_epoch is not None and fitbit_start < rec_start_epoch - 43200:
        shift = 86400.0
        parsed = [(s + shift, e + shift, c) for s, e, c in parsed]
        fitbit_start += shift
        fitbit_end   += shift

    arr = np.full(n_epochs, -1, dtype=int)
    for s_start, s_end, code in parsed:
        for e in range(n_epochs):
            t0 = rec_start_epoch + e * epoch_s
            t1 = t0 + epoch_s
            if t0 < s_end and t1 > s_start:
                arr[e] = code
    arr[arr == -1] = 0  # unmatched → Wake
    return arr, fitbit_start, fitbit_end

# ── Plots ──────────────────────────────────────────────────────────────────────

def plot_hypnogram(hypno: np.ndarray, proba: "pd.DataFrame",
                   rec_start: Optional[float] = None) -> plt.Figure:
    """Hypnogram with per-epoch confidence shading."""
    n = len(hypno)
    t_h = np.arange(n) * EPOCH_S / 3600.0

    # Use YASA int values directly as y positions (W=0, N1=1, N2=2, N3=3, REM=4)
    # then invert y-axis so Wake(0) appears at top, REM(4) at bottom
    y = np.array(hypno, dtype=float)
    conf = proba.max(axis=1).values if proba is not None else np.ones(n)

    fig, (ax_hyp, ax_conf) = plt.subplots(2, 1, figsize=(14, 5),
                                           gridspec_kw={"height_ratios": [3, 1]})

    # Step hypnogram
    for e in range(n - 1):
        color = STAGE_COLORS[INT_TO_LABEL[hypno[e]]]
        ax_hyp.fill_between([t_h[e], t_h[e+1]], y[e] - 0.45, y[e] + 0.45,
                             color=color, alpha=0.85)
        ax_hyp.step([t_h[e], t_h[e+1]], [y[e], y[e]], where="pre",
                    color="black", linewidth=0.6)

    ax_hyp.set_yticks([0, 1, 2, 3, 4])
    ax_hyp.set_yticklabels(["Wake", "N1", "N2", "N3", "REM"])
    ax_hyp.set_ylim(4.5, -0.5)   # invert: Wake(0) at top, REM(4) at bottom
    ax_hyp.set_xlim(t_h[0], t_h[-1])
    ax_hyp.set_xlabel("Time (hours)")
    ax_hyp.set_title("Hypnogram — YASA", fontweight="bold")
    legend = [mpatches.Patch(color=c, label=l) for l, c in STAGE_COLORS.items()]
    ax_hyp.legend(handles=legend, loc="upper right", fontsize=8, ncol=5)

    # Confidence
    ax_conf.fill_between(t_h, conf, alpha=0.7, color="#555")
    ax_conf.set_ylim(0, 1)
    ax_conf.set_xlim(t_h[0], t_h[-1])
    ax_conf.set_ylabel("Confidence")
    ax_conf.set_xlabel("Time (hours)")

    fig.tight_layout()
    return fig


def plot_spindle_gallery(sp_df, raw_data: np.ndarray, fs: float,
                         n_show: int = 12) -> Optional[plt.Figure]:
    if sp_df is None or len(sp_df) == 0:
        return None
    rows = min(n_show, len(sp_df))
    fig, axes = plt.subplots(3, 4, figsize=(14, 8))
    axes = axes.flatten()
    sample = sp_df.sample(min(rows, len(sp_df)), random_state=42)
    for ax, (_, row) in zip(axes, sample.iterrows()):
        s0 = int(row["Start"] * fs)
        s1 = int(row["End"] * fs)
        pad = int(0.5 * fs)
        seg = raw_data[max(0, s0-pad) : s1+pad]
        t = np.arange(len(seg)) / fs - 0.5
        ax.plot(t, seg, linewidth=0.8, color="#3498db")
        ax.axvspan(0, (s1-s0)/fs, color="#3498db", alpha=0.15)
        ax.axhline(0, color="#ccc", linewidth=0.5)
        ax.set_title(f"{row['Frequency']:.1f} Hz  {row['Duration']:.2f}s",
                     fontsize=8)
        ax.set_xlabel("Time (s)", fontsize=7)
        ax.tick_params(labelsize=7)
    for ax in axes[rows:]:
        ax.set_visible(False)
    fig.suptitle(f"Sleep Spindles (n={len(sp_df)})  — sample of {rows}",
                 fontweight="bold")
    fig.tight_layout()
    return fig


def plot_slow_wave_gallery(sw_df, raw_data: np.ndarray, fs: float,
                           n_show: int = 12) -> Optional[plt.Figure]:
    if sw_df is None or len(sw_df) == 0:
        return None
    rows = min(n_show, len(sw_df))
    fig, axes = plt.subplots(3, 4, figsize=(14, 8))
    axes = axes.flatten()
    sample = sw_df.sample(min(rows, len(sw_df)), random_state=42)
    for ax, (_, row) in zip(axes, sample.iterrows()):
        s0 = int(row["Start"] * fs)
        s1 = int(row["End"] * fs)
        pad = int(0.5 * fs)
        seg = raw_data[max(0, s0-pad) : s1+pad]
        t = np.arange(len(seg)) / fs - 0.5
        ax.plot(t, seg, linewidth=0.8, color="#1abc9c")
        ax.axhline(0, color="#ccc", linewidth=0.5)
        ax.set_title(f"PTP {row['PTP']:.0f} µV  {row['Duration']:.2f}s",
                     fontsize=8)
        ax.set_xlabel("Time (s)", fontsize=7)
        ax.tick_params(labelsize=7)
    for ax in axes[rows:]:
        ax.set_visible(False)
    fig.suptitle(f"Slow Waves (n={len(sw_df)})  — sample of {rows}",
                 fontweight="bold")
    fig.tight_layout()
    return fig


def plot_summary(hypno: np.ndarray, stats: dict) -> plt.Figure:
    fig, (ax_pie, ax_txt) = plt.subplots(1, 2, figsize=(11, 5))

    counts = {INT_TO_LABEL[i]: int(np.sum(hypno == i)) for i in range(5)}
    total_min = len(hypno) * EPOCH_S / 60
    labels = [k for k, v in counts.items() if v > 0]
    sizes  = [counts[k] * EPOCH_S / 60 for k in labels]
    colors = [STAGE_COLORS[k] for k in labels]
    ax_pie.pie(sizes, labels=labels, colors=colors, autopct="%1.0f%%",
               startangle=90, textprops={"fontsize": 9})
    ax_pie.set_title(f"Sleep stage distribution\n"
                     f"(total {total_min:.0f} min = {total_min/60:.1f} h)",
                     fontweight="bold")

    lines = [
        f"Total sleep time:    {stats.get('TST', 0):.0f} min",
        f"Sleep efficiency:    {stats.get('SE', 0):.1f}%",
        f"Sleep onset lat.:    {stats.get('SOL', 0):.0f} min",
        f"REM latency:         {stats.get('REM_lat', stats.get('Rlat', 0)):.0f} min",
        f"Wake after sleep:    {stats.get('WASO', 0):.0f} min",
        f"",
        f"N1:   {stats.get('%N1', 0):.1f}%",
        f"N2:   {stats.get('%N2', 0):.1f}%",
        f"N3:   {stats.get('%N3', 0):.1f}%",
        f"REM:  {stats.get('%REM', 0):.1f}%",
        f"",
        f"Spindle count:  (see gallery)",
        f"Slow wave count: (see gallery)",
    ]
    ax_txt.axis("off")
    ax_txt.text(0.05, 0.95, "\n".join(lines), transform=ax_txt.transAxes,
                fontsize=10, verticalalignment="top", fontfamily="monospace")
    ax_txt.set_title("Sleep statistics", fontweight="bold")
    fig.tight_layout()
    return fig


def plot_comparison(eeg_hypno: np.ndarray, fitbit_hypno: np.ndarray,
                    t_hours: np.ndarray) -> plt.Figure:
    from sklearn.metrics import cohen_kappa_score

    # 4-stage mapping for kappa: Wake=0, Light(N1+N2)=1, Deep(N3)=2, REM=3
    def _to_4stage(h):
        out = np.zeros(len(h), dtype=int)
        out[h == 1] = 1   # N1 → Light
        out[h == 2] = 1   # N2 → Light
        out[h == 3] = 2   # N3 → Deep
        out[h == 4] = 3   # REM
        return out

    eeg4 = _to_4stage(eeg_hypno)
    fit4 = _to_4stage(fitbit_hypno)
    mask = fitbit_hypno >= 0
    kappa = cohen_kappa_score(fit4[mask], eeg4[mask])

    fig, axes = plt.subplots(2, 1, figsize=(14, 6), sharex=True)
    label_map = {0: "Wake", 1: "Light", 2: "Deep", 3: "REM"}
    color_map  = {0: "#e74c3c", 1: "#3498db", 2: "#1abc9c", 3: "#9b59b6"}
    for ax, hypno4, title in zip(axes,
                                  [fit4, eeg4],
                                  ["Fitbit ground truth", "EEG (YASA)"]):
        for e in range(len(hypno4) - 1):
            c = color_map[hypno4[e]]
            y = hypno4[e]
            ax.fill_between([t_hours[e], t_hours[e+1]], y-0.4, y+0.4,
                            color=c, alpha=0.85)
        ax.set_yticks([0, 1, 2, 3])
        ax.set_yticklabels(["Wake", "Light", "Deep", "REM"])
        ax.set_ylim(3.5, -0.5)   # invert: Wake(0) at top, REM(3) at bottom
        ax.set_title(title, fontweight="bold")
        ax.set_xlim(t_hours[0], t_hours[-1])

    axes[1].set_xlabel("Time (hours)")
    fig.suptitle(f"EEG vs Fitbit  |  Cohen's κ = {kappa:.3f}", fontweight="bold")
    fig.tight_layout()
    return fig

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="YASA-based sleep analysis")
    ap.add_argument("--edf",    required=True,  help="Path to EDF recording")
    ap.add_argument("--fitbit", default=None,   help="Fitbit ground truth JSON")
    ap.add_argument("--out",    default=None,   help="Output directory for figures")
    ap.add_argument("--fmt",    default="png",  choices=["png", "pdf", "svg"])
    ap.add_argument("--age",    type=int, default=None, help="Subject age (improves YASA)")
    ap.add_argument("--male",   action="store_true",   help="Subject is male")
    ap.add_argument("--eeg",    default=EEG_CH, help=f"EEG channel name (default {EEG_CH})")
    args = ap.parse_args()

    try:
        import yasa
    except ImportError:
        print("ERROR: yasa not installed. Run:  pip install yasa")
        sys.exit(1)
    try:
        import sklearn  # noqa — needed by plot_comparison kappa
    except ImportError:
        print("ERROR: scikit-learn not installed. Run:  pip install scikit-learn")
        sys.exit(1)

    # ── Output dir ────────────────────────────────────────────────────────────
    stem = os.path.splitext(os.path.basename(args.edf))[0]
    out_dir = args.out or os.path.join(os.path.dirname(__file__), "figures")
    os.makedirs(out_dir, exist_ok=True)

    def _save(fig, tag):
        if fig is None:
            return
        path = os.path.join(out_dir, f"{stem}_yasa_{tag}.{args.fmt}")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  → {path}")

    # ── Load ──────────────────────────────────────────────────────────────────
    raw = load_raw(args.edf)
    rec_start = load_rec_start(args.edf)
    fs  = raw.info["sfreq"]

    # ── Pre-parse Fitbit to get window bounds for cropping ────────────────────
    # Do this before YASA so we can crop raw first — Fitbit dictates start,
    # either device dictates end (whichever finishes first).
    gt = None
    fitbit_start_epoch = None
    fitbit_end_epoch   = None
    if args.fitbit and rec_start is not None:
        with open(args.fitbit) as f:
            gt = json.load(f)
        # n_epochs=1 is a dummy — we only care about fitbit_start/end here
        _, fitbit_start_epoch, fitbit_end_epoch = parse_fitbit(
            gt, rec_start, n_epochs=1, epoch_s=EPOCH_S)

    # ── Crop raw: Fitbit dictates start; end = min(EEG end, Fitbit end) ───────
    rec_start_eff = rec_start   # effective rec_start after any crop
    if rec_start is not None and fitbit_start_epoch is not None:
        tmin_crop = max(0.0, fitbit_start_epoch - rec_start)
        tmax_crop = min(raw.times[-1], fitbit_end_epoch - rec_start)
        if tmin_crop > 0 or tmax_crop < raw.times[-1]:
            raw.crop(tmin=tmin_crop, tmax=tmax_crop)
            rec_start_eff = rec_start + tmin_crop
            print(f"  Cropped to Fitbit window: "
                  f"{tmin_crop/3600:.2f}–{tmax_crop/3600:.2f} h from rec_start "
                  f"({raw.n_times/raw.info['sfreq']/3600:.2f} h remaining)")

    # Trim to exact epoch boundary so hypno length matches data length in YASA.
    # MNE crop(tmax=t) is inclusive, so subtract one sample to get exactly
    # n_epochs * EPOCH_S * fs samples (no partial epoch dangling at the end).
    n_epochs_max = int(raw.n_times // (EPOCH_S * fs))
    trim_tmax = n_epochs_max * EPOCH_S - 1.0 / fs
    raw.crop(tmax=trim_tmax)

    # ── YASA staging ──────────────────────────────────────────────────────────
    print("\n[STEP 1] Running YASA sleep staging...")
    metadata = {}
    if args.age  is not None: metadata["age"]  = args.age
    if args.male:              metadata["male"] = True
    if not metadata:
        print("  TIP: pass --age and --male for better YASA accuracy")

    sls = yasa.SleepStaging(raw, eeg_name=args.eeg,
                            eog_name=EOG_CH if EOG_CH in raw.ch_names else None,
                            emg_name=EMG_CH if EMG_CH in raw.ch_names else None,
                            metadata=metadata if metadata else None)
    hypno_str  = sls.predict()
    proba      = sls.predict_proba()
    hypno      = np.array([YASA_TO_INT[s] for s in hypno_str])
    conf_mean  = proba.max(axis=1).values.mean()

    counts = {INT_TO_LABEL[i]: int(np.sum(hypno == i)) for i in range(5)}
    print(f"  Stage counts (epochs): {counts}")
    print(f"  Mean confidence: {conf_mean:.2f}")

    stats = yasa.sleep_statistics(hypno, sf_hyp=1.0 / EPOCH_S)
    print(f"  TST={stats.get('TST',0):.0f} min  "
          f"SE={stats.get('SE',0):.1f}%  "
          f"REM={stats.get('%REM',0):.1f}%  "
          f"N3={stats.get('%N3',0):.1f}%")

    # Upsample epoch-level hypno to data sampling rate (required by detection fns)
    hypno_up = yasa.hypno_upsample_to_data(hypno, sf_hypno=1.0/EPOCH_S, data=raw)

    # ── Spindle detection ─────────────────────────────────────────────────────
    print("\n[STEP 2] Detecting sleep spindles (YASA)...")
    try:
        sp = yasa.spindles_detect(raw, ch_names=[args.eeg],
                                  hypno=hypno_up, include=(2,))
        sp_df = sp.summary() if sp is not None else None
        n_sp  = len(sp_df) if sp_df is not None else 0
        print(f"  Found {n_sp} spindles")
    except Exception as e:
        print(f"  Spindle detection failed: {e}")
        sp_df = None

    # ── Slow wave detection ───────────────────────────────────────────────────
    print("\n[STEP 3] Detecting slow waves (YASA)...")
    try:
        sw = yasa.sw_detect(raw, ch_names=[args.eeg],
                            hypno=hypno_up, include=(3,))
        sw_df = sw.summary() if sw is not None else None
        n_sw  = len(sw_df) if sw_df is not None else 0
        print(f"  Found {n_sw} slow waves")
    except Exception as e:
        print(f"  Slow wave detection failed: {e}")
        sw_df = None

    # Raw EEG array for galleries
    eeg_data = raw.get_data(picks=[args.eeg])[0] * 1e6   # V → µV

    # ── Fitbit ───────────────────────────────────────────────────────────────
    fitbit_hypno = None
    if args.fitbit:
        print(f"\n[STEP 4] Loading Fitbit ground truth: {args.fitbit}")
        if rec_start is None:
            print("  WARNING: no meta.json found — Fitbit alignment unavailable")
        elif gt is None:
            # rec_start exists but Fitbit was loaded without rec_start (shouldn't happen)
            with open(args.fitbit) as f:
                gt = json.load(f)
        if gt is not None and rec_start is not None:
            # Use rec_start_eff so epoch 0 aligns with the cropped window start
            fitbit_hypno, _, _ = parse_fitbit(gt, rec_start_eff, len(hypno), EPOCH_S)
            if fitbit_hypno is not None:
                fb_counts = {INT_TO_LABEL[i]: int(np.sum(fitbit_hypno == i))
                             for i in range(5)}
                print(f"  Fitbit stage counts: {fb_counts}")

    # ── Plots ─────────────────────────────────────────────────────────────────
    print("\n[STEP 5] Generating figures...")
    t_hours = np.arange(len(hypno)) * EPOCH_S / 3600.0

    _save(plot_hypnogram(hypno, proba, rec_start), "hypnogram")

    # YASA built-in spectrogram
    try:
        fig_sg = yasa.plot_spectrogram(eeg_data, fs, hypno=hypno_up,
                                        fmin=0.5, fmax=25)
        fig_sg.set_size_inches(14, 6)
        fig_sg.suptitle(f"Spectrogram — {args.eeg}", fontweight="bold")
        _save(fig_sg, "spectrogram")
    except Exception as e:
        print(f"  Spectrogram failed: {e}")

    _save(plot_spindle_gallery(sp_df, eeg_data, fs), "spindles")
    _save(plot_slow_wave_gallery(sw_df, eeg_data, fs), "slow_waves")
    _save(plot_summary(hypno, stats), "summary")

    if fitbit_hypno is not None:
        try:
            _save(plot_comparison(hypno, fitbit_hypno, t_hours), "comparison")
        except ImportError:
            print("  Skipping comparison (scikit-learn not installed)")

    print(f"\nDone. Figures saved to {out_dir}/")


if __name__ == "__main__":
    main()
