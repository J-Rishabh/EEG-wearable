#!/usr/bin/env python3
"""
plot_rssi_experiments.py

Make thesis-ready RSSI plots from one or more CSV files created by your BLE RSSI logger.

Features
--------
- Supports logger files with column name "rssi"
- Backwards-compatible with older files using "rssi_dbm"
- Aggregates by trial first, then across trials
- Produces:
    1) RSSI vs distance (mean ± 95% CI)
    2) Trial means scatter + overall mean
    3) RSSI vs log10(distance) with optional linear fit
- Gracefully skips log-distance fitting if there are not enough valid points

Install
-------
pip install pandas matplotlib numpy

Run
---
python plot_rssi_experiments.py rssi_logs
python plot_rssi_experiments.py rssi_logs --output-dir thesis_plots
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def load_data(path_str: str) -> pd.DataFrame:
    path = Path(path_str)

    if path.is_dir():
        files = sorted(path.glob("*.csv"))
    else:
        files = [path]

    if not files:
        raise FileNotFoundError(f"No CSV files found in {path}")

    dfs = []
    for f in files:
        df = pd.read_csv(f)
        df["source_file"] = f.name
        dfs.append(df)

    out = pd.concat(dfs, ignore_index=True)

    if "rssi_dbm" in out.columns:
        out["rssi_dbm"] = pd.to_numeric(out["rssi_dbm"], errors="coerce")
    elif "rssi" in out.columns:
        out["rssi_dbm"] = pd.to_numeric(out["rssi"], errors="coerce")
    else:
        raise ValueError("No RSSI column found. Expected 'rssi' or 'rssi_dbm'.")

    if "distance_m" not in out.columns:
        raise ValueError("Missing required column: 'distance_m'")
    if "condition" not in out.columns:
        raise ValueError("Missing required column: 'condition'")
    if "trial" not in out.columns:
        raise ValueError("Missing required column: 'trial'")

    out["distance_m"] = pd.to_numeric(out["distance_m"], errors="coerce")
    out["condition"] = out["condition"].astype(str).str.strip()
    out["trial"] = out["trial"].astype(str).str.strip()

    out = out.replace([np.inf, -np.inf], np.nan)
    out = out.dropna(subset=["distance_m", "rssi_dbm", "condition", "trial"])

    return out


def summarize(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    trial_means = (
        df.groupby(["condition", "distance_m", "trial"], as_index=False)
        .agg(
            rssi_mean_dbm=("rssi_dbm", "mean"),
            rssi_std_dbm=("rssi_dbm", "std"),
            n_packets=("rssi_dbm", "size"),
        )
        .sort_values(["condition", "distance_m", "trial"])
    )

    summary = (
        trial_means.groupby(["condition", "distance_m"], as_index=False)
        .agg(
            mean_rssi_dbm=("rssi_mean_dbm", "mean"),
            std_across_trials_dbm=("rssi_mean_dbm", "std"),
            n_trials=("rssi_mean_dbm", "size"),
        )
        .sort_values(["condition", "distance_m"])
    )

    summary["std_across_trials_dbm"] = summary["std_across_trials_dbm"].fillna(0.0)
    summary["sem_dbm"] = summary["std_across_trials_dbm"] / np.sqrt(summary["n_trials"].clip(lower=1))
    summary["ci95_dbm"] = 1.96 * summary["sem_dbm"]
    summary["sem_dbm"] = summary["sem_dbm"].fillna(0.0)
    summary["ci95_dbm"] = summary["ci95_dbm"].fillna(0.0)

    return trial_means, summary


def make_plot_mean(summary: pd.DataFrame, output_dir: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8.6, 5.6), dpi=200)

    for condition in summary["condition"].drop_duplicates():
        sub = summary[summary["condition"] == condition].sort_values("distance_m")
        ax.errorbar(
            sub["distance_m"],
            sub["mean_rssi_dbm"],
            yerr=sub["ci95_dbm"],
            marker="o",
            linewidth=2,
            capsize=4,
            label=condition,
        )

    ax.set_xlabel("Distance (m)")
    ax.set_ylabel("RSSI (dBm)")
    ax.set_title("RSSI vs Distance by Condition")
    ax.grid(True, alpha=0.3)
    ax.legend(title="Condition")
    fig.tight_layout()

    out = output_dir / "rssi_vs_distance_mean_ci.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def make_plot_trial_scatter(
    trial_means: pd.DataFrame,
    summary: pd.DataFrame,
    output_dir: Path,
) -> Path:
    fig, ax = plt.subplots(figsize=(8.6, 5.6), dpi=200)

    rng = np.random.default_rng(0)

    for condition in summary["condition"].drop_duplicates():
        sub_trials = trial_means[trial_means["condition"] == condition].sort_values("distance_m")
        sub_summary = summary[summary["condition"] == condition].sort_values("distance_m")

        jitter = rng.normal(0.0, 0.015, size=len(sub_trials))
        ax.scatter(
            sub_trials["distance_m"] + jitter,
            sub_trials["rssi_mean_dbm"],
            alpha=0.45,
            s=28,
            label=f"{condition} trial means",
        )
        ax.plot(
            sub_summary["distance_m"],
            sub_summary["mean_rssi_dbm"],
            marker="o",
            linewidth=2.5,
            label=f"{condition} overall mean",
        )

    ax.set_xlabel("Distance (m)")
    ax.set_ylabel("Per-trial mean RSSI (dBm)")
    ax.set_title("Trial Means and Overall Means")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()

    out = output_dir / "rssi_trial_means_scatter.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def make_plot_log_distance(summary: pd.DataFrame, output_dir: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8.6, 5.6), dpi=200)

    any_fit = False

    for condition in summary["condition"].drop_duplicates():
        sub = summary[
            (summary["condition"] == condition) &
            (summary["distance_m"] > 0)
        ].copy()

        if len(sub) == 0:
            print(f"Skipping {condition}: no positive-distance points")
            continue

        x = np.log10(sub["distance_m"].to_numpy(dtype=float))
        y = sub["mean_rssi_dbm"].to_numpy(dtype=float)

        mask = np.isfinite(x) & np.isfinite(y)
        x = x[mask]
        y = y[mask]

        if len(x) == 0:
            print(f"Skipping {condition}: no finite points")
            continue

        ax.plot(
            x,
            y,
            marker="o",
            linewidth=2,
            label=condition,
        )

        if len(x) >= 2 and len(np.unique(x)) >= 2:
            try:
                coeffs = np.polyfit(x, y, 1)
                fit = np.poly1d(coeffs)

                x_fit = np.linspace(x.min(), x.max(), 100)
                ax.plot(x_fit, fit(x_fit), linestyle="--", alpha=0.6)

                slope = coeffs[0]
                intercept = coeffs[1]
                print(
                    f"{condition}: fitted line RSSI = {intercept:.3f} "
                    f"+ ({slope:.3f})*log10(d)"
                )
                any_fit = True
            except np.linalg.LinAlgError:
                print(f"Skipping fit for {condition}: polyfit failed")
        else:
            print(f"Skipping fit for {condition}: need at least 2 distinct distances")

    ax.set_xlabel("log10(Distance in m)")
    ax.set_ylabel("Mean RSSI (dBm)")
    if any_fit:
        ax.set_title("RSSI vs log10(Distance)")
    else:
        ax.set_title("RSSI vs log10(Distance) (fit skipped: insufficient distance variation)")
    ax.grid(True, alpha=0.3)
    ax.legend(title="Condition")
    fig.tight_layout()

    out = output_dir / "rssi_vs_log_distance.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def save_summary_tables(
    trial_means: pd.DataFrame,
    summary: pd.DataFrame,
    output_dir: Path,
) -> tuple[Path, Path]:
    trial_path = output_dir / "trial_means_summary.csv"
    summary_path = output_dir / "condition_distance_summary.csv"
    trial_means.to_csv(trial_path, index=False)
    summary.to_csv(summary_path, index=False)
    return trial_path, summary_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="CSV file or directory of CSV files")
    parser.add_argument(
        "--output-dir",
        default="thesis_plots",
        help="Where to save figures and summary CSVs",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    df = load_data(args.input)
    print(f"Loaded {len(df)} raw RSSI samples")

    trial_means, summary = summarize(df)
    print(f"Computed {len(trial_means)} trial-level summaries")
    print(f"Computed {len(summary)} condition-distance summaries")

    p1 = make_plot_mean(summary, output_dir)
    p2 = make_plot_trial_scatter(trial_means, summary, output_dir)
    p3 = make_plot_log_distance(summary, output_dir)
    s1, s2 = save_summary_tables(trial_means, summary, output_dir)

    print("Saved:")
    for p in [p1, p2, p3, s1, s2]:
        print(f"  {p}")


if __name__ == "__main__":
    main()