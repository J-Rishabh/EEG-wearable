from __future__ import annotations

"""
Record Arduino AD8232 ECG + PulseSensor data to CSV with timestamp metadata.

Time-synced with EEG recordings from eeg_stream_pg.py — both save a
rec_start_epoch (Unix wall-clock float) so analyze_ecg_accuracy.py can
align the two streams even if they weren't started at exactly the same time.

Output (default: python/recordings/ — same folder as EEG EDF files):
  arduino_ecg_YYYYMMDD_HHMMSS.csv       columns: ecg_adc, pulse_adc, leads_off
  arduino_ecg_YYYYMMDD_HHMMSS_meta.json same schema as eeg_*_meta.json

Usage:
    python record_arduino_ecg.py                       # auto-detect port, Ctrl-C to stop
    python record_arduino_ecg.py --port COM3
    python record_arduino_ecg.py --port COM3 --duration 60
    python record_arduino_ecg.py --out /custom/output/dir
"""

import argparse
import csv
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime

import serial
import serial.tools.list_ports

BAUD_RATE   = 115200
SAMPLE_RATE = 1000   # Hz — matches the 1 ms delay in the Arduino sketch


# ── Port detection ────────────────────────────────────────────────────────────

def auto_detect_port() -> str:
    ports = serial.tools.list_ports.comports()
    for p in ports:
        desc = (p.description or "").lower()
        if any(k in desc for k in ("arduino", "ch340", "ch341", "cp210", "ftdi", "uno")):
            return p.device
    if ports:
        return ports[0].device
    raise RuntimeError("No serial ports found. Connect the Arduino and try again.")


# ── Serial reader thread ──────────────────────────────────────────────────────

class SerialRecorder(threading.Thread):
    """Reads 'ecg_adc,pulse_adc\n' lines from the AD8232 Arduino sketch."""

    def __init__(self, port: str, baud: int):
        super().__init__(daemon=True)
        self.ser      = serial.Serial(port, baud, timeout=1)
        self._lock    = threading.Lock()
        self._samples: list[tuple[int, int, int]] = []
        self._running = True

    def run(self):
        while self._running:
            try:
                line = self.ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                parts      = line.split(",")
                leads_off  = parts[0] == "!"
                pulse_val  = int(parts[1]) if len(parts) > 1 else 512
                ecg_val    = 512 if leads_off else int(parts[0])
                with self._lock:
                    self._samples.append((ecg_val, pulse_val, int(leads_off)))
            except (ValueError, IndexError, serial.SerialException):
                continue

    def stop(self):
        self._running = False

    def sample_count(self) -> int:
        with self._lock:
            return len(self._samples)

    def get_samples(self) -> list[tuple[int, int, int]]:
        with self._lock:
            return list(self._samples)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Record AD8232 ECG + PulseSensor to CSV (time-synced with EEG recordings)"
    )
    parser.add_argument("--port",     default=None,  help="Serial port, e.g. COM3 or /dev/ttyUSB0")
    parser.add_argument("--duration", type=float, default=None,
                        help="Recording duration in seconds (default: until Ctrl-C)")
    parser.add_argument("--out",      default=None,
                        help="Output directory (default: python/recordings/ next to eeg_stream_pg.py)")
    args = parser.parse_args()

    port = args.port or auto_detect_port()
    print(f"Connecting to {port} at {BAUD_RATE} baud...")

    recorder  = SerialRecorder(port, BAUD_RATE)
    recorder.start()

    rec_start = time.time()
    ts        = datetime.fromtimestamp(rec_start).strftime("%Y%m%d_%H%M%S")

    # Output directory: default to python/recordings/ so it sits next to EDF files
    if args.out:
        out_dir = args.out
    else:
        # This file lives at python/experiments/ecg/ — go up three levels to python/
        here    = os.path.dirname(os.path.abspath(__file__))
        out_dir = os.path.normpath(os.path.join(here, "..", "..", "recordings"))
    os.makedirs(out_dir, exist_ok=True)

    csv_name  = f"arduino_ecg_{ts}.csv"
    meta_name = f"arduino_ecg_{ts}_meta.json"
    csv_path  = os.path.join(out_dir, csv_name)
    meta_path = os.path.join(out_dir, meta_name)

    print(f"Recording started: {datetime.fromtimestamp(rec_start).isoformat()}")
    print(f"Output: {csv_path}")
    if args.duration:
        print(f"Duration: {args.duration:.0f} s")
    else:
        print("Press Ctrl-C to stop.\n")

    stop_event = threading.Event()

    def _sigint(_sig, _frame):
        stop_event.set()

    signal.signal(signal.SIGINT, _sigint)

    try:
        while not stop_event.is_set():
            if args.duration and (time.time() - rec_start) >= args.duration:
                break
            elapsed = time.time() - rec_start
            n       = recorder.sample_count()
            print(
                f"\r  {elapsed:6.1f} s | {n:7d} samples ({n / SAMPLE_RATE:.1f} s @ {SAMPLE_RATE} Hz)   ",
                end="", flush=True,
            )
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass

    recorder.stop()
    print("\n\nStopping recorder...")
    time.sleep(0.3)   # let thread drain its last line

    samples = recorder.get_samples()
    if not samples:
        print("No samples recorded — nothing saved.")
        return

    # Write CSV ----------------------------------------------------------------
    with open(csv_path, "w", newline="") as cf:
        writer = csv.writer(cf)
        writer.writerow(["ecg_adc", "pulse_adc", "leads_off"])
        writer.writerows(samples)

    # Write meta JSON — same schema as eeg_*_meta.json -------------------------
    meta = {
        "rec_start_epoch": rec_start,
        "rec_start_iso":   datetime.fromtimestamp(rec_start).isoformat(),
        "n_samples":       len(samples),
        "fs":              SAMPLE_RATE,
        "csv_file":        csv_name,
        "device":          "AD8232 + PulseSensor (Arduino Uno R3)",
        "columns":         ["ecg_adc (0-1023 ADC)", "pulse_adc (0-1023 ADC)", "leads_off (0/1)"],
    }
    with open(meta_path, "w") as mf:
        json.dump(meta, mf, indent=2)

    duration = len(samples) / SAMPLE_RATE
    print(f"Saved {len(samples)} samples ({duration:.1f} s)  ->  {csv_path}")
    print(f"Meta                                      ->  {meta_path}")
    print(f"\nNext step — run accuracy analysis:")
    print(f"  python analyze_ecg_accuracy.py --arduino {meta_path} --eeg <path/to/eeg_*_meta.json>")


if __name__ == "__main__":
    main()
