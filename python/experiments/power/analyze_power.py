#!/usr/bin/env python3
"""
analyze_power.py

Process PPK2 current-measurement CSV files to characterize power consumption
across three device operating modes:
    1) BLE_ADV_ADS_ON      – advertising, no connection, ADS1299 analog powered (STOPPED)
    2) BLE_ADV_ADS_OFF     – advertising, no connection, ADS1299 powered down (~65 µA)
    3) BLE_ADV_ADS_OFF_LED – advertising, ADS1299 off, LED also off (cleanest idle baseline)
    4) BLE_CONNECTED       – connected and streaming EEG data at 250 SPS

Expected input (one CSV per mode, placed in ppk2_data/):
    ble_adv_ads_on.csv
    ble_adv_ads_off.csv
    ble_adv_ads_off_led_off.csv
    ble_connected.csv

PPK2 export format (Nordic Power Profiler App v2):
    - Two-column CSV: "Timestamp(ms)" and "Current(uA)"
    - OR single-column "Current(uA)" with implicit 100 Hz spacing
    The script handles both.

Output
------
    figures/transient_overlay.png   – 10 s of raw current traces, all modes
    figures/histogram.png           – current distribution (PDF) per mode
    figures/boxplot.png             – box-and-whisker comparison
    figures/psd.png                 – power spectral density (reveals BLE periodicity)
    power_summary.csv               – summary statistics table

Run
---
    python analyze_power.py
    python analyze_power.py --data-dir ppk2_data --out-dir figures --voltage 3.7
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from scipy import signal as scipy_signal

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SAMPLE_RATE_HZ = 100          # PPK2 export rate
SUPPLY_VOLTAGE_V = 3.7        # default; overridable via CLI
BATTERY_MAH = 200             # 200 mAh battery for life estimate
TRANSIENT_WINDOW_S = 10       # seconds of data to show in transient plot
HIGH_CURRENT_THRESH_MA = 5.0  # duty-cycle threshold (mA)

MODE_LABELS = {
    "ble_adv_ads_on":        "BLE Advertising (ADS on, LED on)",
    "ble_adv_ads_off":       "BLE Advertising (ADS off, LED on)",
    "ble_adv_ads_off_led_off": "BLE Advertising (ADS off, LED off)",
    "ble_connected":         "BLE Connected and streaming samples",
}
MODE_COLORS = {
    "ble_adv_ads_on":          "#DD8452",
    "ble_adv_ads_off":         "#4C72B0",
    "ble_adv_ads_off_led_off": "#9467BD",
    "ble_connected":           "#55A868",
}
MODE_FILES = {
    "ble_adv_ads_on":          "ble_adv_ads_on.csv",
    "ble_adv_ads_off":         "ble_adv_ads_off.csv",
    "ble_adv_ads_off_led_off": "ble_adv_ads_off_led_off.csv",
    "ble_connected":           "ble_connected.csv",
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_ppk2_csv(path: Path) -> np.ndarray:
    """
    Load a PPK2 CSV and return current in mA as a 1-D numpy array.

    Handles:
      - "Timestamp(ms)", "Current(uA)"  – two-column format
      - "Current(uA)"                    – single-column
      - "timestamp_ms", "current_ua"     – lower-case variant
      - Plain single-column with no header (raw µA values)
    """
    df = pd.read_csv(path)

    # Normalise column names to lower-case, strip whitespace and parens
    df.columns = (
        df.columns.str.lower()
                  .str.strip()
                  .str.replace(r"[\(\)]", "", regex=True)
    )

    # Find the current column
    current_col: Optional[str] = None
    for candidate in ("currentua", "current_ua", "currentµa", "current"):
        if candidate in df.columns:
            current_col = candidate
            break

    if current_col is None:
        # Fallback: first numeric column
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        if not numeric_cols:
            raise ValueError(f"Cannot find a numeric current column in {path}")
        current_col = numeric_cols[0]
        print(f"  [warn] using column '{current_col}' as current for {path.name}")

    current_ua = pd.to_numeric(df[current_col], errors="coerce").dropna().to_numpy()
    current_ma = current_ua / 1000.0  # µA → mA
    return current_ma


def load_all_modes(data_dir: Path) -> Dict[str, np.ndarray]:
    """Load CSV for each mode; skip gracefully if a file is missing."""
    data: Dict[str, np.ndarray] = {}
    for key, fname in MODE_FILES.items():
        fpath = data_dir / fname
        if not fpath.exists():
            print(f"[skip] {fpath} not found — mode '{key}' excluded")
            continue
        print(f"[load] {fpath}")
        arr = load_ppk2_csv(fpath)
        print(f"       {len(arr)} samples  ({len(arr)/SAMPLE_RATE_HZ:.1f} s)")
        data[key] = arr
    return data


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def compute_stats(
    data: Dict[str, np.ndarray], voltage_v: float, battery_mah: float
) -> pd.DataFrame:
    """
    Return a summary DataFrame with one row per mode.

    Columns
    -------
    Mode, Avg Current (mA), Peak Current (mA), Std Dev (mA),
    Avg Power (mW), Battery Life (h)
    """
    rows: List[dict] = []

    for key, arr in data.items():
        avg_ma   = float(np.mean(arr))
        peak_ma  = float(np.max(arr))
        std_ma   = float(np.std(arr))
        avg_mw   = avg_ma * voltage_v

        # Expected battery life
        if avg_ma > 0:
            batt_life_h = battery_mah / avg_ma
        else:
            batt_life_h = float("inf")

        rows.append({
            "Mode":                  MODE_LABELS.get(key, key),
            "Avg Current (mA)":      round(avg_ma, 4),
            "Peak Current (mA)":     round(peak_ma, 4),
            "Std Dev (mA)":          round(std_ma, 4),
            "Avg Power (mW)":        round(avg_mw, 4),
            "Est. Battery Life (h)": round(batt_life_h, 2),
        })

    df = pd.DataFrame(rows)
    return df


# ---------------------------------------------------------------------------
# Plotting helpers
# ---------------------------------------------------------------------------

def _time_axis(n_samples: int) -> np.ndarray:
    return np.arange(n_samples) / SAMPLE_RATE_HZ


def plot_transient(
    data: Dict[str, np.ndarray], out_path: Path, window_s: float = TRANSIENT_WINDOW_S
) -> None:
    """
    Overlay 10 s of raw current traces for all modes.
    Each mode is offset slightly on the y-axis for readability (ghost traces),
    and also shown on its own y-axis via a secondary twin approach.
    We keep it simple: single y-axis, thin alpha-blended lines + solid mean band.
    """
    n_samples = int(window_s * SAMPLE_RATE_HZ)
    fig, ax = plt.subplots(figsize=(12, 5))

    for key, arr in data.items():
        seg = arr[:n_samples]
        t   = _time_axis(len(seg))
        ax.plot(
            t, seg,
            color=MODE_COLORS[key],
            alpha=0.75,
            linewidth=0.8,
            label=MODE_LABELS.get(key, key),
        )

    ax.set_xlabel("Time (s)", fontsize=12)
    ax.set_ylabel("Current (mA)", fontsize=12)
    ax.set_title("Transient Current Draw", fontsize=13)
    ax.legend(loc="upper right", fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(0, window_s)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"[save] {out_path}")


def plot_histogram(data: Dict[str, np.ndarray], out_path: Path) -> None:
    """
    Probability density histogram per mode on a shared axis.
    Log-x helps when there's a wide dynamic range (OFF vs connected).
    """
    fig, axes = plt.subplots(1, len(data), figsize=(5 * len(data), 4), sharey=False)
    if len(data) == 1:
        axes = [axes]  # type: ignore[assignment]

    for ax, (key, arr) in zip(axes, data.items()):
        bins = np.linspace(np.percentile(arr, 0.5), np.percentile(arr, 99.5), 80)
        ax.hist(arr, bins=bins, density=True, color=MODE_COLORS[key], alpha=0.8, edgecolor="none")
        ax.axvline(float(np.mean(arr)), color="black", linestyle="--", linewidth=1.2, label=f"mean={np.mean(arr):.3f} mA")
        ax.set_title(MODE_LABELS.get(key, key), fontsize=11)
        ax.set_xlabel("Current (mA)", fontsize=10)
        ax.set_ylabel("Probability Density", fontsize=10)
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)

    fig.suptitle("Current Distribution per Mode", fontsize=13, y=1.02)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[save] {out_path}")


def plot_boxplot(data: Dict[str, np.ndarray], out_path: Path) -> None:
    """
    Box-and-whisker plot: each mode is one box, y = current (mA).
    Outliers are shown as small dots.  Useful for spotting peak bursts.
    """
    keys   = list(data.keys())
    arrays = [data[k] for k in keys]
    labels = [MODE_LABELS.get(k, k) for k in keys]
    colors = [MODE_COLORS[k] for k in keys]

    fig, ax = plt.subplots(figsize=(8, 5))

    bp = ax.boxplot(
        arrays,
        patch_artist=True,
        notch=False,
        showfliers=True,
        flierprops=dict(marker=".", markersize=1.5, alpha=0.3, linestyle="none"),
        medianprops=dict(color="black", linewidth=1.5),
        whiskerprops=dict(linewidth=1.2),
        capprops=dict(linewidth=1.2),
    )
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)

    ax.set_xticks(range(1, len(labels) + 1))
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel("Current (mA)", fontsize=12)
    ax.set_title("Current Draw Distribution — Box Plot", fontsize=13)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"[save] {out_path}")


def plot_psd(data: Dict[str, np.ndarray], out_path: Path) -> None:
    """
    Power Spectral Density of current draw per mode.

    BLE advertising at ~100 ms intervals → expect a peak near 10 Hz.
    Data-streaming mode may show additional periodic structure.
    Uses Welch's method for a smooth estimate.
    """
    fig, ax = plt.subplots(figsize=(10, 5))

    for key, arr in data.items():
        freqs, psd = scipy_signal.welch(
            arr,
            fs=SAMPLE_RATE_HZ,
            nperseg=min(2048, len(arr) // 4),
            scaling="density",
        )
        ax.semilogy(freqs, psd, color=MODE_COLORS[key], linewidth=1.2, label=MODE_LABELS.get(key, key))

    ax.set_xlabel("Frequency (Hz)", fontsize=12)
    ax.set_ylabel("PSD (mA²/Hz)", fontsize=12)
    ax.set_title("Power Spectral Density of Current Draw", fontsize=13)
    ax.legend(fontsize=10)
    ax.grid(True, which="both", alpha=0.3)
    ax.set_xlim(0, SAMPLE_RATE_HZ / 2)
    # Annotate expected BLE advertising frequency
    ax.axvline(10, color="gray", linestyle=":", linewidth=1, alpha=0.6)
    ax.text(10.3, ax.get_ylim()[0] * 10, "10 Hz\n(BLE adv.)", color="gray", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"[save] {out_path}")


def print_table(df: pd.DataFrame) -> None:
    """Pretty-print the summary table to stdout."""
    col_widths: List[int] = [
        max(len(str(c)), df[c].astype(str).map(len).max())
        for c in df.columns
    ]
    header = "  ".join(str(c).ljust(w) for c, w in zip(df.columns, col_widths))
    sep    = "  ".join("-" * w for w in col_widths)
    print("\n" + header)
    print(sep)
    for _, row in df.iterrows():
        print("  ".join(str(v).ljust(w) for v, w in zip(row, col_widths)))
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze PPK2 power data")
    parser.add_argument(
        "--data-dir", default="ppk2_data",
        help="Directory containing off.csv / ble_search.csv / ble_connected.csv"
    )
    parser.add_argument(
        "--out-dir", default="figures",
        help="Output directory for figures and CSV summary"
    )
    parser.add_argument(
        "--voltage", type=float, default=SUPPLY_VOLTAGE_V,
        help=f"Supply voltage in volts (default: {SUPPLY_VOLTAGE_V})"
    )
    parser.add_argument(
        "--battery-mah", type=float, default=BATTERY_MAH,
        help=f"Battery capacity in mAh for life estimate (default: {BATTERY_MAH})"
    )
    parser.add_argument(
        "--transient-s", type=float, default=TRANSIENT_WINDOW_S,
        help="Duration in seconds for the transient overlay plot"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Resolve paths relative to this script's location
    script_dir = Path(__file__).parent
    data_dir   = script_dir / args.data_dir
    out_dir    = script_dir / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    if not data_dir.exists():
        print(f"[error] data directory not found: {data_dir}")
        sys.exit(1)

    # --- Load ---
    data = load_all_modes(data_dir)
    if not data:
        print("[error] No mode data loaded — place CSV files in ppk2_data/")
        sys.exit(1)

    # --- Plot ---
    plot_transient(data, out_dir / "transient_overlay.png", window_s=args.transient_s)
    plot_histogram(data, out_dir / "histogram.png")
    plot_boxplot(  data, out_dir / "boxplot.png")
    plot_psd(      data, out_dir / "psd.png")

    # --- Stats table ---
    df_stats = compute_stats(data, voltage_v=args.voltage, battery_mah=args.battery_mah)
    summary_path = out_dir / "power_summary.csv"
    df_stats.to_csv(summary_path, index=False)
    print(f"[save] {summary_path}\n")
    print_table(df_stats)


if __name__ == "__main__":
    main()
