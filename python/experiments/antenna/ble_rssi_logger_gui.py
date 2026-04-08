#!/usr/bin/env python3
"""
ble_rssi_logger_gui.py

BLE advertisement RSSI logger for Windows/macOS/Linux using Bleak + PyQt5.

Features
--------
- Scan for nearby BLE advertisers
- Select target device from live table
- Record RSSI samples for a chosen device
- Metadata per run:
    * distance_m
    * condition
    * trial
- Condition is a dropdown (prevents label typos)
- Trial auto-increments after each successful recording
- Trial resets to 1 when condition or distance changes
- Saves all runs into a CSV in ./rssi_logs
- Optional live RSSI plot for the selected device

Install
-------
pip install bleak PyQt5 pandas matplotlib numpy

Run
---
python ble_rssi_logger_gui.py
"""

import sys
import os
import csv
import time
import asyncio
import threading
from datetime import datetime
from collections import deque

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import Qt

try:
    import pyqtgraph as pg
    HAVE_PG = True
except Exception:
    HAVE_PG = False

from bleak import BleakScanner


APP_TITLE = "BLE RSSI Logger"
LOG_DIR = "rssi_logs"
CSV_FILE = os.path.join(LOG_DIR, "rssi_log.csv")

CONDITIONS = [
    "line_of_sight",
    "near_body",
    "body_between",
    "object_between",
]

TABLE_COLUMNS = [
    "Name",
    "Address",
    "RSSI",
    "Seen Count",
    "Last Seen (s ago)",
]

# Optional exact-name filter:
# set to None to allow any BLE device selection
DEFAULT_DEVICE_NAME_FILTER = None
# Example:
# DEFAULT_DEVICE_NAME_FILTER = "EEG Wearable"


class ScannerWorker(QtCore.QObject):
    device_update = QtCore.pyqtSignal(dict)
    log_message = QtCore.pyqtSignal(str)
    scanner_started = QtCore.pyqtSignal()
    scanner_stopped = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._thread = None
        self._loop = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_thread, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(lambda: None)
            except Exception:
                pass

    def _run_thread(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._scan_loop())
        except Exception as e:
            self.log_message.emit(f"[scanner] exception: {e}")
        finally:
            self.scanner_stopped.emit()
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None

    async def _scan_loop(self):
        self.scanner_started.emit()
        self.log_message.emit("[scanner] starting BLE scan")

        def detection_callback(device, advertisement_data):
            if not self._running:
                return

            name = advertisement_data.local_name or device.name or ""
            address = getattr(device, "address", "unknown")
            rssi = getattr(advertisement_data, "rssi", None)

            if rssi is None:
                return

            payload = {
                "name": name,
                "address": address,
                "rssi": int(rssi),
                "timestamp": time.time(),
            }
            self.device_update.emit(payload)

        scanner = BleakScanner(detection_callback=detection_callback)

        try:
            await scanner.start()
            while self._running:
                await asyncio.sleep(0.1)
        finally:
            try:
                await scanner.stop()
            except Exception:
                pass
            self.log_message.emit("[scanner] stopped BLE scan")


class RSSILoggerWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 820)

        os.makedirs(LOG_DIR, exist_ok=True)

        self.devices = {}
        self.selected_address = None

        self.is_recording = False
        self.record_buffer = []
        self.active_condition = None
        self.active_trial = None
        self.active_distance_m = None
        self.active_device_name = None
        self.active_device_address = None
        self.record_start_time = None

        self.rssi_history = deque(maxlen=300)

        self.worker = ScannerWorker()
        self.worker.device_update.connect(self.on_device_update)
        self.worker.log_message.connect(self.append_log)
        self.worker.scanner_started.connect(self.on_scanner_started)
        self.worker.scanner_stopped.connect(self.on_scanner_stopped)

        self._build_ui()
        self._wire_callbacks()

        self.refresh_timer = QtCore.QTimer()
        self.refresh_timer.setInterval(300)
        self.refresh_timer.timeout.connect(self.refresh_table_ages)
        self.refresh_timer.start()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)

        # Top controls
        top_row = QtWidgets.QHBoxLayout()

        self.btn_start_scan = QtWidgets.QPushButton("Start Scanner")
        self.btn_stop_scan = QtWidgets.QPushButton("Stop Scanner")
        self.btn_stop_scan.setEnabled(False)

        self.name_filter_input = QtWidgets.QLineEdit()
        self.name_filter_input.setPlaceholderText("Optional name filter")
        if DEFAULT_DEVICE_NAME_FILTER:
            self.name_filter_input.setText(DEFAULT_DEVICE_NAME_FILTER)

        self.btn_clear_devices = QtWidgets.QPushButton("Clear Device List")

        top_row.addWidget(self.btn_start_scan)
        top_row.addWidget(self.btn_stop_scan)
        top_row.addWidget(QtWidgets.QLabel("Name filter:"))
        top_row.addWidget(self.name_filter_input)
        top_row.addWidget(self.btn_clear_devices)
        root.addLayout(top_row)

        # Device table
        self.device_table = QtWidgets.QTableWidget(0, len(TABLE_COLUMNS))
        self.device_table.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self.device_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.device_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.device_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.device_table.horizontalHeader().setStretchLastSection(True)
        self.device_table.verticalHeader().setVisible(False)
        self.device_table.setAlternatingRowColors(True)
        root.addWidget(self.device_table, stretch=4)

        # Selected device
        selected_row = QtWidgets.QHBoxLayout()
        self.lbl_selected = QtWidgets.QLabel("Selected device: none")
        self.btn_use_selected = QtWidgets.QPushButton("Use Highlighted Device")
        selected_row.addWidget(self.lbl_selected)
        selected_row.addStretch()
        selected_row.addWidget(self.btn_use_selected)
        root.addLayout(selected_row)

        # Metadata row
        meta_row = QtWidgets.QHBoxLayout()

        self.distance_input = QtWidgets.QLineEdit()
        self.distance_input.setPlaceholderText("Distance (m), e.g. 1.0")

        self.condition_input = QtWidgets.QComboBox()
        self.condition_input.addItems(CONDITIONS)

        self.trial_input = QtWidgets.QSpinBox()
        self.trial_input.setMinimum(1)
        self.trial_input.setMaximum(999)
        self.trial_input.setValue(1)

        meta_row.addWidget(QtWidgets.QLabel("Distance (m):"))
        meta_row.addWidget(self.distance_input)
        meta_row.addSpacing(10)
        meta_row.addWidget(QtWidgets.QLabel("Condition:"))
        meta_row.addWidget(self.condition_input)
        meta_row.addSpacing(10)
        meta_row.addWidget(QtWidgets.QLabel("Trial:"))
        meta_row.addWidget(self.trial_input)
        meta_row.addStretch()

        self.lbl_recording_state = QtWidgets.QLabel("Idle")
        meta_row.addWidget(self.lbl_recording_state)

        root.addLayout(meta_row)

        # Record controls
        rec_row = QtWidgets.QHBoxLayout()
        self.btn_start_record = QtWidgets.QPushButton("Start Recording")
        self.btn_stop_record = QtWidgets.QPushButton("Stop Recording")
        self.btn_stop_record.setEnabled(False)

        self.record_seconds_input = QtWidgets.QSpinBox()
        self.record_seconds_input.setMinimum(1)
        self.record_seconds_input.setMaximum(300)
        self.record_seconds_input.setValue(20)

        self.btn_record_fixed = QtWidgets.QPushButton("Record Fixed Duration")

        rec_row.addWidget(self.btn_start_record)
        rec_row.addWidget(self.btn_stop_record)
        rec_row.addSpacing(15)
        rec_row.addWidget(QtWidgets.QLabel("Fixed duration (s):"))
        rec_row.addWidget(self.record_seconds_input)
        rec_row.addWidget(self.btn_record_fixed)
        rec_row.addStretch()
        root.addLayout(rec_row)

        # Plot
        if HAVE_PG:
            self.plot_widget = pg.PlotWidget()
            self.plot_widget.setBackground("w")
            self.plot_widget.setLabel("left", "RSSI (dBm)")
            self.plot_widget.setLabel("bottom", "Recent samples")
            self.plot_widget.showGrid(x=True, y=True, alpha=0.25)
            self.plot_curve = self.plot_widget.plot([], [], pen=pg.mkPen(width=2))
            root.addWidget(self.plot_widget, stretch=2)
        else:
            self.plot_widget = None
            self.plot_curve = None
            root.addWidget(QtWidgets.QLabel("pyqtgraph not installed — live plot disabled"))

        # Log box
        self.log_box = QtWidgets.QPlainTextEdit()
        self.log_box.setReadOnly(True)
        root.addWidget(self.log_box, stretch=2)

    def _wire_callbacks(self):
        self.btn_start_scan.clicked.connect(self.start_scanner)
        self.btn_stop_scan.clicked.connect(self.stop_scanner)
        self.btn_clear_devices.clicked.connect(self.clear_devices)
        self.btn_use_selected.clicked.connect(self.use_highlighted_device)

        self.device_table.itemSelectionChanged.connect(self.on_table_selection_changed)

        self.condition_input.currentIndexChanged.connect(self.reset_trial)
        self.distance_input.editingFinished.connect(self.reset_trial)

        self.btn_start_record.clicked.connect(self.start_recording)
        self.btn_stop_record.clicked.connect(self.stop_recording)
        self.btn_record_fixed.clicked.connect(self.record_fixed_duration)

    def append_log(self, text):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.appendPlainText(f"[{ts}] {text}")

    def current_condition(self):
        return self.condition_input.currentText().strip()

    def current_trial(self):
        return int(self.trial_input.value())

    def increment_trial(self):
        self.trial_input.setValue(self.trial_input.value() + 1)

    def reset_trial(self):
        self.trial_input.setValue(1)

    def parsed_distance(self):
        txt = self.distance_input.text().strip()
        if not txt:
            raise ValueError("Distance is empty")
        val = float(txt)
        if val < 0:
            raise ValueError("Distance must be non-negative")
        return val

    def name_filter(self):
        return self.name_filter_input.text().strip()

    def start_scanner(self):
        self.worker.start()

    def stop_scanner(self):
        self.worker.stop()

    def on_scanner_started(self):
        self.btn_start_scan.setEnabled(False)
        self.btn_stop_scan.setEnabled(True)
        self.append_log("scanner started")

    def on_scanner_stopped(self):
        self.btn_start_scan.setEnabled(True)
        self.btn_stop_scan.setEnabled(False)
        self.append_log("scanner stopped")

    def clear_devices(self):
        self.devices.clear()
        self.device_table.setRowCount(0)
        self.selected_address = None
        self.lbl_selected.setText("Selected device: none")
        self.rssi_history.clear()
        self.update_plot()

    def on_device_update(self, payload):
        name = payload["name"]
        address = payload["address"]
        rssi = payload["rssi"]
        ts = payload["timestamp"]

        filt = self.name_filter()
        if filt:
            if filt.lower() not in (name or "").lower():
                return

        if address not in self.devices:
            self.devices[address] = {
                "name": name,
                "address": address,
                "rssi": rssi,
                "seen_count": 1,
                "last_seen": ts,
            }
        else:
            d = self.devices[address]
            if name:
                d["name"] = name
            d["rssi"] = rssi
            d["seen_count"] += 1
            d["last_seen"] = ts

        self.refresh_table()

        if self.selected_address == address:
            self.rssi_history.append(rssi)
            self.update_plot()

            if self.is_recording:
                self.record_buffer.append({
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
                    "device_name": self.active_device_name,
                    "device_address": self.active_device_address,
                    "rssi": rssi,
                    "distance_m": self.active_distance_m,
                    "condition": self.active_condition,
                    "trial": self.active_trial,
                })
                self.lbl_recording_state.setText(
                    f"Recording: {len(self.record_buffer)} samples"
                )

    def refresh_table(self):
        rows = list(self.devices.values())
        rows.sort(key=lambda x: (x["rssi"], x["seen_count"]), reverse=True)

        self.device_table.setRowCount(len(rows))

        for row_idx, d in enumerate(rows):
            age = max(0.0, time.time() - d["last_seen"])
            values = [
                d["name"] or "(unknown)",
                d["address"],
                str(d["rssi"]),
                str(d["seen_count"]),
                f"{age:.1f}",
            ]
            for col_idx, val in enumerate(values):
                item = QtWidgets.QTableWidgetItem(val)
                if col_idx == 2:
                    item.setTextAlignment(Qt.AlignCenter)
                self.device_table.setItem(row_idx, col_idx, item)

            if self.selected_address == d["address"]:
                self.device_table.selectRow(row_idx)

    def refresh_table_ages(self):
        for row in range(self.device_table.rowCount()):
            address_item = self.device_table.item(row, 1)
            if address_item is None:
                continue
            address = address_item.text()
            if address not in self.devices:
                continue
            age = max(0.0, time.time() - self.devices[address]["last_seen"])
            age_item = self.device_table.item(row, 4)
            if age_item is not None:
                age_item.setText(f"{age:.1f}")

    def on_table_selection_changed(self):
        selected = self.device_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        address_item = self.device_table.item(row, 1)
        name_item = self.device_table.item(row, 0)
        if address_item is None:
            return
        address = address_item.text()
        name = name_item.text() if name_item else "(unknown)"
        self.lbl_selected.setText(f"Highlighted: {name} [{address}]")

    def use_highlighted_device(self):
        selected = self.device_table.selectedItems()
        if not selected:
            QtWidgets.QMessageBox.warning(self, "No device", "Highlight a device row first.")
            return

        row = selected[0].row()
        address = self.device_table.item(row, 1).text()
        name = self.device_table.item(row, 0).text()

        self.selected_address = address
        self.rssi_history.clear()
        self.update_plot()
        self.lbl_selected.setText(f"Selected device: {name} [{address}]")
        self.append_log(f"selected device: {name} [{address}]")

    def update_plot(self):
        if not HAVE_PG or self.plot_curve is None:
            return
        if not self.rssi_history:
            self.plot_curve.setData([], [])
            return
        y = list(self.rssi_history)
        x = list(range(len(y)))
        self.plot_curve.setData(x, y)

    def validate_recording_inputs(self):
        if self.selected_address is None:
            raise ValueError("No selected device")
        distance_m = self.parsed_distance()
        condition = self.current_condition()
        trial = self.current_trial()

        if self.selected_address not in self.devices:
            raise ValueError("Selected device is no longer in the current device table")

        selected_dev = self.devices[self.selected_address]
        device_name = selected_dev["name"] or "(unknown)"
        device_address = selected_dev["address"]

        return distance_m, condition, trial, device_name, device_address

    def start_recording(self):
        if self.is_recording:
            return
        try:
            distance_m, condition, trial, device_name, device_address = self.validate_recording_inputs()
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Cannot start recording", str(e))
            return

        self.is_recording = True
        self.record_buffer = []
        self.record_start_time = time.time()

        self.active_distance_m = distance_m
        self.active_condition = condition
        self.active_trial = trial
        self.active_device_name = device_name
        self.active_device_address = device_address

        self.btn_start_record.setEnabled(False)
        self.btn_stop_record.setEnabled(True)
        self.btn_record_fixed.setEnabled(False)

        self.lbl_recording_state.setText("Recording: 0 samples")
        self.append_log(
            f"recording started | device={device_name} | distance={distance_m} m | "
            f"condition={condition} | trial={trial}"
        )

    def stop_recording(self):
        if not self.is_recording:
            return

        self.is_recording = False
        self.btn_start_record.setEnabled(True)
        self.btn_stop_record.setEnabled(False)
        self.btn_record_fixed.setEnabled(True)

        if not self.record_buffer:
            self.lbl_recording_state.setText("Idle")
            self.append_log("recording stopped: no samples captured")
            return

        out_path = self.save_recording(self.record_buffer)
        n = len(self.record_buffer)
        dt = time.time() - self.record_start_time if self.record_start_time else 0.0

        self.lbl_recording_state.setText(f"Saved {n} samples")
        self.append_log(
            f"recording saved | samples={n} | duration={dt:.1f} s | file={out_path}"
        )

        self.increment_trial()

        self.record_buffer = []
        self.record_start_time = None

    def record_fixed_duration(self):
        if self.is_recording:
            return
        self.start_recording()
        if not self.is_recording:
            return

        duration_s = int(self.record_seconds_input.value())
        self.append_log(f"fixed-duration mode: {duration_s} s")

        QtCore.QTimer.singleShot(duration_s * 1000, self.stop_recording)

    def save_recording(self, samples):
        file_exists = os.path.exists(CSV_FILE)

        with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "timestamp",
                    "device_name",
                    "device_address",
                    "rssi",
                    "distance_m",
                    "condition",
                    "trial",
                ])

            for row in samples:
                writer.writerow([
                    row["timestamp"],
                    row["device_name"],
                    row["device_address"],
                    row["rssi"],
                    row["distance_m"],
                    row["condition"],
                    row["trial"],
                ])

        return CSV_FILE

    def closeEvent(self, event):
        try:
            self.worker.stop()
        except Exception:
            pass
        super().closeEvent(event)


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = RSSILoggerWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()