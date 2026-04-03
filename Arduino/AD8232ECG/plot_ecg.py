from __future__ import annotations

"""
Real-time ECG + PulseSensor plotter for AD8232 + Arduino Uno R3.
Reads serial data, plots the ECG waveform, and calculates heart rate via R-peak detection.
PulseSensor on A1 provides a second BPM estimate; both are averaged for the center display.

Requirements:
    pip install pyserial matplotlib scipy numpy

Usage:
    python plot_ecg.py              # auto-detect port
    python plot_ecg.py --port COM3  # specify port
    python plot_ecg.py --port /dev/ttyUSB0
"""

import argparse
import collections
import threading

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import serial
import serial.tools.list_ports
from scipy.signal import find_peaks, butter, filtfilt


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BAUD_RATE    = 115200
SAMPLE_RATE  = 1000          # Hz  (matches 1 ms delay in Arduino sketch)
WINDOW_SEC   = 5             # seconds of ECG shown in the rolling plot
WINDOW_SIZE  = SAMPLE_RATE * WINDOW_SEC

# Butterworth bandpass for ECG: 0.5–40 Hz
ECG_BP_LOW   = 0.5
ECG_BP_HIGH  = 40.0

# Butterworth bandpass for PPG (PulseSensor): 0.5–5 Hz
PPG_BP_LOW   = 0.5
PPG_BP_HIGH  = 5.0

# R-peak / pulse-peak detection
PEAK_HEIGHT_FRAC = 0.5
PEAK_DISTANCE    = int(SAMPLE_RATE * 0.35)  # min 350 ms between peaks (~170 BPM max)

# BPM smoothing history length
BPM_HISTORY_LEN  = 6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def auto_detect_port() -> str:
    ports = serial.tools.list_ports.comports()
    for p in ports:
        desc = (p.description or "").lower()
        if any(k in desc for k in ("arduino", "ch340", "ch341", "cp210", "ftdi", "uno")):
            return p.device
    if ports:
        return ports[0].device
    raise RuntimeError("No serial ports found. Plug in the Arduino and try again.")


def bandpass_filter(data: np.ndarray, fs: float, low: float, high: float) -> np.ndarray:
    nyq = fs / 2.0
    b, a = butter(4, [low / nyq, high / nyq], btype="band")
    return filtfilt(b, a, data)


def detect_peaks_and_bpm(signal: np.ndarray, fs: float) -> tuple[np.ndarray, float | None]:
    if len(signal) < PEAK_DISTANCE * 2:
        return np.array([], dtype=int), None

    med    = np.median(signal)
    rng    = np.percentile(signal, 95) - np.percentile(signal, 5)
    height = med + PEAK_HEIGHT_FRAC * rng

    peaks, _ = find_peaks(signal, height=height, distance=PEAK_DISTANCE)

    if len(peaks) < 2:
        return peaks, None

    rr_intervals = np.diff(peaks) / fs
    bpm = 60.0 / np.mean(rr_intervals)
    return peaks, bpm


def bpm_color(bpm: float) -> str:
    return "#3fb950" if 50 <= bpm <= 100 else "#f0883e"


# ---------------------------------------------------------------------------
# Serial reader thread — parses "ecg,pulse" CSV lines
# ---------------------------------------------------------------------------
class SerialReader(threading.Thread):
    def __init__(self, port: str, baud: int):
        super().__init__(daemon=True)
        self.ser       = serial.Serial(port, baud, timeout=1)
        self.ecg_buf   = collections.deque(maxlen=WINDOW_SIZE)
        self.pulse_buf = collections.deque(maxlen=WINDOW_SIZE)
        self.leads_off = False
        self._lock     = threading.Lock()

    def run(self):
        _dbg = 0
        while True:
            try:
                line = self.ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                parts = line.split(",")
                leads_off = parts[0] == "!"
                pulse_val = int(parts[1]) if len(parts) > 1 else 512
                with self._lock:
                    self.leads_off = leads_off
                    self.pulse_buf.append(pulse_val)
                    if not leads_off:
                        self.ecg_buf.append(int(parts[0]))
                _dbg += 1
                if _dbg % 1000 == 0:
                    print(f"[PPG] last={pulse_val}  leads_off={leads_off}  buf_len={len(self.pulse_buf)}")
            except (ValueError, IndexError, serial.SerialException):
                continue

    def snapshot(self) -> tuple[np.ndarray, np.ndarray, bool]:
        with self._lock:
            return (
                np.array(self.ecg_buf,   dtype=float),
                np.array(self.pulse_buf, dtype=float),
                self.leads_off,
            )


# ---------------------------------------------------------------------------
# Main plotter
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Real-time AD8232 ECG + PulseSensor plotter")
    parser.add_argument("--port", default=None, help="Serial port (e.g. COM3 or /dev/ttyUSB0)")
    args = parser.parse_args()

    port = args.port or auto_detect_port()
    print(f"Connecting to {port} at {BAUD_RATE} baud…")
    reader = SerialReader(port, BAUD_RATE)
    reader.start()
    print("Connected. Waiting for data…")

    # --- Layout -----------------------------------------------------------
    # Row 0: ECG trace (full width)
    # Row 1: PPG trace (full width)
    # Row 2: BPM panel (left) | R-R trend (right)
    fig = plt.figure(figsize=(13, 8), facecolor="#0d1117")
    fig.canvas.manager.set_window_title("ECG + PulseSensor Monitor")

    gs = gridspec.GridSpec(3, 2,
                           height_ratios=[3, 2, 1.4],
                           hspace=0.45, wspace=0.3,
                           left=0.07, right=0.97, top=0.92, bottom=0.07)

    ax_ecg  = fig.add_subplot(gs[0, :])
    ax_ppg  = fig.add_subplot(gs[1, :])
    ax_bpm  = fig.add_subplot(gs[2, 0])
    ax_rr   = fig.add_subplot(gs[2, 1])

    dark_axes = (ax_ecg, ax_ppg, ax_bpm, ax_rr)
    for ax in dark_axes:
        ax.set_facecolor("#161b22")
        for spine in ax.spines.values():
            spine.set_edgecolor("#30363d")
        ax.tick_params(colors="#8b949e")
        ax.yaxis.label.set_color("#8b949e")
        ax.xaxis.label.set_color("#8b949e")
        ax.title.set_color("#e6edf3")

    t_axis = np.linspace(-WINDOW_SEC, 0, WINDOW_SIZE)

    # ECG trace
    (ecg_raw_line,)  = ax_ecg.plot(t_axis, np.zeros(WINDOW_SIZE), color="#58a6ff", lw=1.2, label="raw")
    (ecg_filt_line,) = ax_ecg.plot(t_axis, np.zeros(WINDOW_SIZE), color="#3fb950", lw=1.0, alpha=0.7, label="filtered")
    ecg_peaks_sc     = ax_ecg.scatter([], [], color="#f85149", s=60, zorder=5, label="R-peaks")
    ax_ecg.set_xlim(-WINDOW_SEC, 0)
    ax_ecg.set_xlabel("Time (s)")
    ax_ecg.set_ylabel("ADC")
    ax_ecg.set_title("ECG — AD8232 (A0)", fontsize=11, fontweight="bold")
    ax_ecg.legend(loc="upper right", facecolor="#161b22", labelcolor="#e6edf3", fontsize=8)
    ax_ecg.grid(True, color="#21262d", linewidth=0.6)
    status_text = ax_ecg.text(0.01, 0.95, "", transform=ax_ecg.transAxes,
                              fontsize=9, va="top", color="#f85149", fontweight="bold")

    # PPG trace
    (ppg_raw_line,)  = ax_ppg.plot(t_axis, np.zeros(WINDOW_SIZE), color="#d2a8ff", lw=1.2, label="raw")
    (ppg_filt_line,) = ax_ppg.plot(t_axis, np.zeros(WINDOW_SIZE), color="#f0883e", lw=1.0, alpha=0.7, label="filtered")
    ppg_peaks_sc     = ax_ppg.scatter([], [], color="#f85149", s=60, zorder=5, label="peaks")
    ax_ppg.set_xlim(-WINDOW_SEC, 0)
    ax_ppg.set_xlabel("Time (s)")
    ax_ppg.set_ylabel("ADC")
    ax_ppg.set_title("PPG — PulseSensor (A1)", fontsize=11, fontweight="bold")
    ax_ppg.legend(loc="upper right", facecolor="#161b22", labelcolor="#e6edf3", fontsize=8)
    ax_ppg.grid(True, color="#21262d", linewidth=0.6)

    # BPM panel — three numbers
    ax_bpm.set_xlim(0, 1)
    ax_bpm.set_ylim(0, 1)
    ax_bpm.axis("off")

    # Small left: ECG BPM
    ax_bpm.text(0.05, 0.92, "ECG", ha="left", va="top",
                fontsize=8, color="#8b949e", transform=ax_bpm.transAxes)
    ecg_bpm_text = ax_bpm.text(0.05, 0.55, "--", ha="left", va="center",
                                fontsize=18, fontweight="bold", color="#58a6ff",
                                transform=ax_bpm.transAxes)
    ax_bpm.text(0.05, 0.12, "BPM", ha="left", va="bottom",
                fontsize=8, color="#8b949e", transform=ax_bpm.transAxes)

    # Small right: PPG BPM
    ax_bpm.text(0.95, 0.92, "PPG", ha="right", va="top",
                fontsize=8, color="#8b949e", transform=ax_bpm.transAxes)
    ppg_bpm_text = ax_bpm.text(0.95, 0.55, "--", ha="right", va="center",
                                fontsize=18, fontweight="bold", color="#d2a8ff",
                                transform=ax_bpm.transAxes)
    ax_bpm.text(0.95, 0.12, "BPM", ha="right", va="bottom",
                fontsize=8, color="#8b949e", transform=ax_bpm.transAxes)

    # Large center: averaged BPM
    avg_bpm_text  = ax_bpm.text(0.5, 0.58, "--", ha="center", va="center",
                                 fontsize=34, fontweight="bold", color="#3fb950",
                                 transform=ax_bpm.transAxes)
    ax_bpm.text(0.5, 0.15, "Avg BPM", ha="center", va="bottom",
                fontsize=9, color="#8b949e", transform=ax_bpm.transAxes)

    # Divider lines
    ax_bpm.axvline(0.28, color="#30363d", lw=1)
    ax_bpm.axvline(0.72, color="#30363d", lw=1)

    # R-R trend
    rr_history: collections.deque = collections.deque(maxlen=30)
    (rr_line,) = ax_rr.plot([], [], color="#d2a8ff", lw=1.5, marker="o", markersize=3)
    ax_rr.set_xlabel("Beat #")
    ax_rr.set_ylabel("R-R (ms)")
    ax_rr.set_title("R-R Interval (ECG)", fontsize=10)
    ax_rr.grid(True, color="#21262d", linewidth=0.6)

    ecg_bpm_history: collections.deque = collections.deque(maxlen=BPM_HISTORY_LEN)
    ppg_bpm_history: collections.deque = collections.deque(maxlen=BPM_HISTORY_LEN)

    # --- Animation loop ---------------------------------------------------
    def update(_):
        ecg_raw, ppg_raw, leads_off = reader.snapshot()

        status_text.set_text("LEADS OFF — check electrode placement" if leads_off else "")

        def pad(arr):
            if len(arr) == 0:
                return np.full(WINDOW_SIZE, 512)
            if len(arr) < WINDOW_SIZE:
                out = np.full(WINDOW_SIZE, arr[0])
                out[-len(arr):] = arr
                return out
            return arr[-WINDOW_SIZE:]

        ppg_padded = pad(ppg_raw)

        # --- PPG always updates ---
        try:
            ppg_filt = bandpass_filter(ppg_padded, SAMPLE_RATE, PPG_BP_LOW, PPG_BP_HIGH)
        except Exception:
            ppg_filt = ppg_padded.copy()

        ppg_raw_line.set_ydata(ppg_padded)
        ppg_filt_line.set_ydata(ppg_filt + 512 - np.median(ppg_filt))

        p5p, p95p = np.percentile(ppg_padded, 5), np.percentile(ppg_padded, 95)
        marginp = max((p95p - p5p) * 0.3, 20)
        ax_ppg.set_ylim(p5p - marginp, p95p + marginp)

        # --- ECG freezes when leads are off ---
        ecg_bpm = None
        if not leads_off and len(ecg_raw) > 0:
            ecg_padded = pad(ecg_raw)
            try:
                ecg_filt = bandpass_filter(ecg_padded, SAMPLE_RATE, ECG_BP_LOW, ECG_BP_HIGH)
            except Exception:
                ecg_filt = ecg_padded.copy()

            ecg_raw_line.set_ydata(ecg_padded)
            ecg_filt_line.set_ydata(ecg_filt + 512 - np.median(ecg_filt))

            p5, p95 = np.percentile(ecg_padded, 5), np.percentile(ecg_padded, 95)
            margin = max((p95 - p5) * 0.3, 20)
            ax_ecg.set_ylim(p5 - margin, p95 + margin)

            ecg_peaks, ecg_bpm = detect_peaks_and_bpm(ecg_filt, SAMPLE_RATE)
            if len(ecg_peaks):
                ecg_peaks_sc.set_offsets(np.c_[t_axis[ecg_peaks], ecg_padded[ecg_peaks]])
                rr_ms = np.diff(ecg_peaks) / SAMPLE_RATE * 1000
                for rr in rr_ms:
                    rr_history.append(rr)
            else:
                ecg_peaks_sc.set_offsets(np.empty((0, 2)))
        else:
            ecg_peaks_sc.set_offsets(np.empty((0, 2)))

        # PPG peak detection on RAW signal — PulseSensor has onboard hardware filtering
        # so a software bandpass on top kills the peaks. Use percentile threshold instead.
        ppg_std = np.std(ppg_padded)
        ppg_good = ppg_std > 8.0   # flat signal = no contact

        ppg_bpm = None
        if ppg_good:
            ppg_thresh = np.percentile(ppg_padded, 65)
            ppg_peaks, _ = find_peaks(ppg_padded, height=ppg_thresh,
                                      prominence=ppg_std * 0.5,
                                      distance=PEAK_DISTANCE)
            ppg_bpm = (60.0 / np.mean(np.diff(ppg_peaks) / SAMPLE_RATE)) if len(ppg_peaks) >= 2 else None
            if len(ppg_peaks):
                ppg_peaks_sc.set_offsets(np.c_[t_axis[ppg_peaks], ppg_padded[ppg_peaks]])
            else:
                ppg_peaks_sc.set_offsets(np.empty((0, 2)))
        else:
            ppg_bpm_history.clear()
            ppg_peaks_sc.set_offsets(np.empty((0, 2)))

        # Smooth BPM histories
        # Clear ECG history while leads are off so stale values don't linger
        if leads_off:
            ecg_bpm_history.clear()
        elif ecg_bpm is not None:
            ecg_bpm_history.append(ecg_bpm)

        if ppg_bpm is not None:
            ppg_bpm_history.append(ppg_bpm)

        smooth_ecg = np.mean(ecg_bpm_history) if ecg_bpm_history else None
        smooth_ppg = np.mean(ppg_bpm_history) if ppg_bpm_history else None

        ecg_bpm_text.set_text(f"{smooth_ecg:.0f}" if smooth_ecg is not None else "--")
        ppg_bpm_text.set_text(f"{smooth_ppg:.0f}" if smooth_ppg is not None else "--")

        # Average — only use sensors that are active
        available = [b for b in (smooth_ecg if not leads_off else None, smooth_ppg) if b is not None]
        if available:
            avg = np.mean(available)
            avg_bpm_text.set_text(f"{avg:.0f}")
            avg_bpm_text.set_color(bpm_color(avg))
        else:
            avg_bpm_text.set_text("--")

        # R-R trend
        if len(rr_history) >= 2:
            rr_arr = list(rr_history)
            rr_line.set_data(range(len(rr_arr)), rr_arr)
            ax_rr.set_xlim(0, max(1, len(rr_arr) - 1))
            ax_rr.set_ylim(min(rr_arr) * 0.9, max(rr_arr) * 1.1)

        return (ecg_raw_line, ecg_filt_line, ecg_peaks_sc,
                ppg_raw_line, ppg_filt_line, ppg_peaks_sc,
                avg_bpm_text, ecg_bpm_text, ppg_bpm_text, rr_line, status_text)

    from matplotlib.animation import FuncAnimation
    ani = FuncAnimation(fig, update, interval=50, blit=False, cache_frame_data=False)

    plt.suptitle("ECG + PulseSensor Monitor", fontsize=14, fontweight="bold",
                 color="#e6edf3", y=0.97)
    plt.show()


if __name__ == "__main__":
    main()
