#!/usr/bin/env python3
"""
eeg_stream_pg_raw_channels.py  —  Raw per-channel viewer (each vs SRB1)
=======================================================================
Shows CH1–CH7 exactly as output by the ADS1299 — each channel is already
referenced against SRB1 in hardware, so what you see here is (INx+ − SRB1)
with no cross-channel subtraction applied.  Use this for crosstalk tests,
electrode placement checks, or any time you want the unmanipulated inputs.

CH8 (BIASOUT_DRN) is shown at the bottom as BIAS, same as the main viewer.

Run:
    python eeg_stream_pg_raw_channels.py           # live BLE
    python eeg_stream_pg_raw_channels.py --test    # synthetic data (no hardware)

Keyboard shortcuts
------------------
  n           toggle 60 Hz notch on / off
  b           cycle band: Full → Delta → Theta → Alpha → Beta → Gamma → …
  u           toggle raw (unfiltered) mode
  f           toggle live PSD
  w           cycle window: 5 s → 10 s → 30 s → 1 min
  r           toggle Record / Stop
  s           Stop + Save EDF immediately
  1–8         select row  (1=CH1 … 7=CH7, 8=BIAS)
  + / -       zoom in / out on selected row
  scroll      scroll wheel on signal panel zooms row under cursor
  q           quit
"""

import sys
import os
import argparse
import threading
import time
import struct
from collections import deque
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtWidgets, QtCore, QtGui
from scipy.signal import welch, butter, iirnotch, sosfilt, sosfilt_zi, tf2sos

from eeg_ble import BleEEGClient, DEVICE_NAME
from eeg_processing import BAND_RANGES, estimate_hr_live

pg.setConfigOptions(antialias=False, useOpenGL=True)

# ──────────────────────────────────────────────────────────────────────────────
# TOP-LEVEL CONFIGURATION
# ──────────────────────────────────────────────────────────────────────────────

TEST_MODE = False
FS        = 250
IMU_FS    = 25

EDF_FLUSH_INTERVAL_S = 60

from eeg_motion import (
    ImuMotionDetector,
    MOTION_THRESHOLD_MG, MOTION_HOLDOFF_S, IMU_GRAVITY_ALPHA, IMU_DYNAMIC_ALPHA,
)

MAX_DEQUE     = 15_000
WINDOW_SEC    = [5, 10, 30, 60]
WINDOW_LABELS = ["5 s", "10 s", "30 s", "1 min"]
DEFAULT_WIN   = 1

# Raw channel labels — CH1–CH7 each referenced against SRB1 in hardware
RAW_CH_LABELS = [
    "CH1 EOG",
    "CH2 EMG_far",
    "CH3 EMG_near",
    "CH4 EEG_L1",
    "CH5 EEG_L2",
    "CH6 EEG_L3",
    "CH7 SRB1_ref",
]
N_RAW = len(RAW_CH_LABELS)   # 7

DISPLAY_ROWS = RAW_CH_LABELS + ["BIAS"]
N_ROWS       = len(DISPLAY_ROWS)   # 8

ROW_COLORS = {
    "CH1 EOG":      "#4fc3f7",
    "CH2 EMG_far":  "#ef5350",
    "CH3 EMG_near": "#ffa726",
    "CH4 EEG_L1":   "#66bb6a",
    "CH5 EEG_L2":   "#ab47bc",
    "CH6 EEG_L3":   "#26c6da",
    "CH7 SRB1_ref": "#90a4ae",
    "BIAS":         "#78909c",
}

DEFAULT_SCALE_UV = {
    "CH1 EOG":      500.0,
    "CH2 EMG_far":  1000.0,
    "CH3 EMG_near": 1000.0,
    "CH4 EEG_L1":   200.0,
    "CH5 EEG_L2":   200.0,
    "CH6 EEG_L3":   200.0,
    "CH7 SRB1_ref": 100.0,
    "BIAS":          10.0,
}

# Hardware gain groups — same physical groupings as the main viewer
GAIN_VALUES        = [1, 2, 4, 6, 8, 12, 24]
GAIN_SLIDER_LABELS = ["CH1  EOG", "CH2/3  EMG", "CH4/6  EEG L1/L3", "CH5  EEG L2"]
_GAIN_ROWS = {
    0: ["CH1 EOG"],
    1: ["CH2 EMG_far", "CH3 EMG_near"],
    2: ["CH4 EEG_L1", "CH6 EEG_L3"],
    3: ["CH5 EEG_L2"],
}

ZOOM_FACTOR = 1.5


# ──────────────────────────────────────────────────────────────────────────────
# 7-CHANNEL FILTER  (same stages as EEGProcessor but n_ch=7)
# ──────────────────────────────────────────────────────────────────────────────

class RawChannelProcessor:
    """Per-channel IIR filter chain for 7 raw ADS1299 channels.

    Identical stages to EEGProcessor (notch, HP, LP/bandpass) but operates
    on 7 columns instead of 6 derived rows.
    """

    BAND_MODES = list(BAND_RANGES.keys())

    def __init__(self, fs: float = 250.0):
        self.fs        = fs
        self.notch_on  = True
        self.band_mode = "Full"
        self._build_filters()
        self._reset_zi()

    def set_notch(self, enabled: bool):
        self.notch_on = enabled
        self._reset_zi()

    def set_band(self, mode: str):
        if mode not in BAND_RANGES:
            raise ValueError(f"Unknown band mode '{mode}'")
        self.band_mode = mode
        self._reset_zi()

    def reset_state(self):
        self._reset_zi()

    def process(self, raw_uv: np.ndarray) -> np.ndarray:
        """Filter a batch of shape (N, 7); returns (N, 7)."""
        if raw_uv.ndim != 2 or raw_uv.shape[1] != N_RAW:
            raise ValueError(
                f"process() expects shape (N, {N_RAW}), got {raw_uv.shape}"
            )
        out = np.ascontiguousarray(raw_uv)

        if self.notch_on:
            out, self._zi_notch = sosfilt(
                self._sos_notch, out, axis=0, zi=self._zi_notch
            )
        out, self._zi_hp = sosfilt(
            self._sos_hp, out, axis=0, zi=self._zi_hp
        )
        sos_lp = self._sos_lp_full if self.band_mode == "Full" \
                 else self._sos_band[self.band_mode]
        out, self._zi_lp = sosfilt(
            sos_lp, out, axis=0, zi=self._zi_lp
        )
        return out

    def _build_filters(self):
        fs  = self.fs
        nyq = fs / 2.0

        b_notch, a_notch = iirnotch(60.0, Q=30.0, fs=fs)
        self._sos_notch  = tf2sos(b_notch, a_notch)
        self._sos_hp     = butter(2, 0.5 / nyq, btype="high", output="sos")
        self._sos_lp_full = butter(4, 40.0 / nyq, btype="low",  output="sos")

        self._sos_band = {}
        for name, freqs in BAND_RANGES.items():
            if freqs is None:
                continue
            lo, hi = freqs
            lo = max(lo, 0.1)
            hi = min(hi, nyq - 0.5)
            self._sos_band[name] = butter(
                4, [lo / nyq, hi / nyq], btype="band", output="sos"
            )

    def _reset_zi(self):
        def _make_zi(sos):
            template = sosfilt_zi(sos)
            return np.zeros((*template.shape, N_RAW), dtype=np.float64)

        self._zi_notch = _make_zi(self._sos_notch)
        self._zi_hp    = _make_zi(self._sos_hp)
        sos_lp = self._sos_lp_full if self.band_mode == "Full" \
                 else self._sos_band[self.band_mode]
        self._zi_lp    = _make_zi(sos_lp)


# ──────────────────────────────────────────────────────────────────────────────
# NUMPY RING BUFFER
# ──────────────────────────────────────────────────────────────────────────────

class _RingBuf:
    """Pre-allocated circular numpy buffer. Not thread-safe — protect externally."""
    def __init__(self, capacity: int, width: int = 1):
        self._cap   = capacity
        self._w     = width
        self._buf   = np.zeros((capacity, width) if width > 1 else (capacity,),
                               dtype=np.float64)
        self._head  = 0
        self._count = 0

    def push(self, rows: np.ndarray):
        n = len(rows)
        h = self._head
        tail = h + n
        if tail <= self._cap:
            self._buf[h:tail] = rows
        else:
            split = self._cap - h
            self._buf[h:]         = rows[:split]
            self._buf[:n - split] = rows[split:]
        self._head  = tail % self._cap
        self._count = min(self._count + n, self._cap)

    def last(self, n: int) -> np.ndarray:
        n     = min(n, self._count)
        end   = self._head
        start = (end - n) % self._cap
        if start < end:
            return self._buf[start:end].copy()
        return np.concatenate([self._buf[start:], self._buf[:end]])

    def __len__(self):
        return self._count


# ──────────────────────────────────────────────────────────────────────────────
# SHARED STATE
# ──────────────────────────────────────────────────────────────────────────────

class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.pending: deque = deque()
        self.filt_buf  = _RingBuf(MAX_DEQUE, N_RAW)   # 7 raw channels filtered
        self.raw_d_buf = _RingBuf(MAX_DEQUE, N_RAW)   # 7 raw channels unfiltered
        self.bias_buf  = _RingBuf(MAX_DEQUE)            # CH8 BIASOUT_DRN
        self.rails     = np.zeros(8, dtype=bool)
        self.window_idx   = DEFAULT_WIN
        self.show_raw     = False
        self.band_mode    = "Full"
        self.fft_live     = False
        self.selected_row = 0
        self.row_scales   = dict(DEFAULT_SCALE_UV)
        self.gain         = [24, 24, 24, 24]
        self.recording    = False
        self.rec_buf      = []
        self.imu_rec_buf  = []
        self.rec_start    = None
        self.hw_test_mode = False
        self.motion_active        = False
        self.motion_holdoff_until = 0.0
        self.imu_x    = 0
        self.imu_y    = 0
        self.imu_z    = 0
        self.imu_temp = 0
        self.drl_active   = True
        self.ble_gap_start = None
        self.edf_writer   = None
        self.edf_path     = None
        self.edf_ts       = None
        self.vbat_mv      = 0
        self.vbat_pct     = 0
        self.pmic_charging = False
        self.pmic_error    = False

    @property
    def window_samples(self) -> int:
        return WINDOW_SEC[self.window_idx] * FS


STATE = SharedState()


# ──────────────────────────────────────────────────────────────────────────────
# SYNTHETIC TEST DATA
# ──────────────────────────────────────────────────────────────────────────────

def test_data_thread(stop_evt: threading.Event):
    """Generate a distinct sine wave on each raw channel for visual testing."""
    rng  = np.random.default_rng(0)
    dt   = 1.0 / FS
    t    = 0.0
    BATCH = 8
    # One distinct frequency per channel so you can see crosstalk as sidebands
    freqs  = [0.3, 1.2, 5.0, 10.0, 13.0, 20.0, 0.05]   # Hz, CH1-CH7
    amps   = [200, 800, 150, 40, 40, 40, 10]              # µV
    phases = [i * 0.7 for i in range(7)]
    while not stop_evt.is_set():
        batch = []
        for _ in range(BATCH):
            sigs = [
                amps[i] * np.sin(2 * np.pi * freqs[i] * t + phases[i])
                + rng.normal(0, 3)
                for i in range(7)
            ]
            bias = rng.normal(0, 4)
            batch.append(np.array(sigs + [bias], dtype=np.float64))
            t += dt
        with STATE.lock:
            STATE.pending.extend(batch)
            if STATE.recording:
                STATE.rec_buf.extend(batch)
        time.sleep(BATCH * dt * 0.92)


# ──────────────────────────────────────────────────────────────────────────────
# BLE CLIENT
# ──────────────────────────────────────────────────────────────────────────────

def _on_ble_sample(uv, gains, rails, t_pkt):
    with STATE.lock:
        STATE.pending.append(uv)
        STATE.rails |= rails
        if STATE.recording:
            STATE.rec_buf.extend(uv)


def _on_pmic(vbat_mv, pct, charging, error, rssi):
    with STATE.lock:
        STATE.vbat_mv       = vbat_mv
        STATE.vbat_pct      = pct
        STATE.pmic_charging = charging
        STATE.pmic_error    = error


_imu_detector = ImuMotionDetector(
    threshold_mg=MOTION_THRESHOLD_MG,
    holdoff_s=MOTION_HOLDOFF_S,
    gravity_alpha=IMU_GRAVITY_ALPHA,
    dynamic_alpha=IMU_DYNAMIC_ALPHA,
)


def _on_imu_sample(x_mg, y_mg, z_mg, temp_cdeg):
    _dyn_raw, _dyn_smooth, motion_active = _imu_detector.process_sample(x_mg, y_mg, z_mg)
    with STATE.lock:
        STATE.motion_active = motion_active
        STATE.imu_x    = x_mg
        STATE.imu_y    = y_mg
        STATE.imu_z    = z_mg
        STATE.imu_temp = temp_cdeg
        if STATE.recording:
            STATE.imu_rec_buf.append(
                (x_mg, y_mg, z_mg, 1 if motion_active else 0, temp_cdeg)
            )


BLE_CLIENT = BleEEGClient()
BLE_CLIENT.set_sample_callback(_on_ble_sample)
BLE_CLIENT.set_pmic_callback(_on_pmic)
BLE_CLIENT.set_imu_callback(_on_imu_sample)


# ──────────────────────────────────────────────────────────────────────────────
# EDF SAVE  (saves raw 8-channel data — identical to main viewer)
# ──────────────────────────────────────────────────────────────────────────────

def _edf_open(rec_start: float, gain: list):
    try:
        import pyedflib
    except ImportError:
        print("[EDF] pyedflib not found — incremental save unavailable")
        return None, None, None

    rec_dir = os.path.join(os.path.dirname(__file__), "recordings")
    os.makedirs(rec_dir, exist_ok=True)
    ts    = datetime.fromtimestamp(rec_start).strftime("%Y%m%d_%H%M%S")
    fname = os.path.join(rec_dir, f"eeg_{ts}.edf")

    ch_gain    = [gain[0], gain[1], gain[1], gain[2], gain[3], gain[2], 24, 1]
    eeg_ranges = [int(4_500_000 / g) for g in ch_gain]
    eeg_labels = ["EOG","EMG_far","EMG_near","EEG_L1","EEG_L2","EEG_L3","SRB1","DRL"]
    imu_labels = ["ACCEL_X","ACCEL_Y","ACCEL_Z","MOTION","IMU_TEMP"]
    imu_ranges = [2000.0, 2000.0, 2000.0, 1.0, 8500.0]
    imu_dims   = ["mg",   "mg",   "mg",   "bool", "cdeg"]

    try:
        f = pyedflib.EdfWriter(fname, 13, file_type=pyedflib.FILETYPE_EDFPLUS)
        f.setStartdatetime(datetime.fromtimestamp(rec_start))
        for i in range(8):
            f.setSignalHeader(i, {
                "label": eeg_labels[i], "dimension": "uV",
                "sample_frequency": FS,
                "physical_max":  eeg_ranges[i], "physical_min": -eeg_ranges[i],
                "digital_max": 32767,    "digital_min": -32768,
                "prefilter": "HP:0.5Hz LP:40Hz N:60Hz",
                "transducer": "ADS1299",
            })
        for j in range(5):
            f.setSignalHeader(8 + j, {
                "label": imu_labels[j], "dimension": imu_dims[j],
                "sample_frequency": IMU_FS,
                "physical_max":  imu_ranges[j], "physical_min": -imu_ranges[j],
                "digital_max": 32767,            "digital_min": -32768,
                "prefilter": "", "transducer": "LIS2DW12",
            })
        print(f"[EDF] Opened for incremental write: {fname}")
        return f, fname, ts
    except Exception as e:
        print(f"[EDF] Failed to open writer: {e}")
        return None, None, None


def _edf_flush_to_disk(writer, n_eeg_to_write: int, n_imu_to_write: int):
    with STATE.lock:
        eeg_snap = STATE.rec_buf[:n_eeg_to_write]
        imu_snap = STATE.imu_rec_buf[:n_imu_to_write]

    if not eeg_snap:
        return

    arr = np.array(eeg_snap, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 8:
        return

    has_imu = bool(imu_snap)
    signals = [np.ascontiguousarray(arr[:, i]) for i in range(8)]
    if has_imu:
        imu_arr = np.array(imu_snap, dtype=np.float64)
        for j in range(5):
            signals.append(np.ascontiguousarray(imu_arr[:, j]))
    else:
        for _ in range(5):
            signals.append(np.zeros(n_imu_to_write))

    try:
        writer.writeSamples(signals)
    except Exception as e:
        print(f"[EDF] writeSamples failed: {e}"); return

    with STATE.lock:
        del STATE.rec_buf[:n_eeg_to_write]
        del STATE.imu_rec_buf[:n_imu_to_write]

    print(f"[EDF] Flushed {n_eeg_to_write // FS} s to disk")


def _edf_close(writer, path: str, ts: str, rec_start: float):
    if writer is None:
        return

    with STATE.lock:
        buf     = list(STATE.rec_buf)
        imu_buf = list(STATE.imu_rec_buf)

    arr = np.array(buf, dtype=np.float64) if buf else np.empty((0, 8))
    has_imu = bool(imu_buf)
    if has_imu:
        imu_arr = np.array(imu_buf, dtype=np.float64)
        n_secs  = min(len(arr) // FS, len(imu_arr) // IMU_FS)
    else:
        n_secs  = len(arr) // FS

    if n_secs > 0:
        n_eeg = n_secs * FS
        n_imu = n_secs * IMU_FS
        signals = [np.ascontiguousarray(arr[:n_eeg, i]) for i in range(8)]
        if has_imu:
            imu_arr_w = np.array(imu_buf, dtype=np.float64)
            for j in range(5):
                signals.append(np.ascontiguousarray(imu_arr_w[:n_imu, j]))
        else:
            for _ in range(5):
                signals.append(np.zeros(n_imu))
        try:
            writer.writeSamples(signals)
        except Exception as e:
            print(f"[EDF] Final flush failed: {e}")

    try:
        writer.close()
    except Exception as e:
        print(f"[EDF] Writer close failed: {e}")

    import json
    meta = {
        "rec_start_epoch": rec_start,
        "rec_start_iso":   datetime.fromtimestamp(rec_start).isoformat(),
        "n_samples":       n_secs * FS,
        "fs":              FS,
        "edf_file":        os.path.basename(path),
        "imu_fs":          IMU_FS,
        "imu_channels":    ["ACCEL_X","ACCEL_Y","ACCEL_Z","MOTION","IMU_TEMP"],
    }
    rec_dir   = os.path.dirname(path)
    meta_path = os.path.join(rec_dir, f"eeg_{ts}_meta.json")
    with open(meta_path, "w") as mf:
        json.dump(meta, mf, indent=2)

    print(f"[EDF] Closed: {path}")
    print(f"[EDF] meta -> {meta_path}")


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _hex_alpha(hex_color: str, alpha: float) -> tuple:
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return (r, g, b, int(alpha * 255))


_BTN_STYLE = """
    QPushButton {{
        background-color: {bg};
        color: {fg};
        border: 1px solid #2a2a2a;
        padding: 2px 6px;
        font-size: 8pt;
        font-family: monospace;
    }}
    QPushButton:hover {{ background-color: #2a2a2a; }}
"""

_LABEL_STYLE = "color: {fg}; font-size: 8pt; font-family: monospace;"


def _btn(text: str, fg: str = "#cccccc", bg: str = "#1a1a1a") -> QtWidgets.QPushButton:
    b = QtWidgets.QPushButton(text)
    b.setStyleSheet(_BTN_STYLE.format(bg=bg, fg=fg))
    b.setFocusPolicy(QtCore.Qt.NoFocus)
    return b


def _lbl(text: str, fg: str = "#666666") -> QtWidgets.QLabel:
    lb = QtWidgets.QLabel(text)
    lb.setStyleSheet(_LABEL_STYLE.format(fg=fg))
    lb.setAlignment(QtCore.Qt.AlignCenter)
    return lb


def _fmt_scale(sc_uv: float) -> str:
    if sc_uv >= 1000.0:
        mv  = sc_uv / 1000.0
        dec = max(0, 3 - max(0, int(np.log10(mv))))
        return f"{mv:.{dec}f} mV"
    else:
        dec = max(0, 3 - max(0, int(np.log10(max(sc_uv, 1.0)))))
        return f"{sc_uv:.{dec}f} \u03bcV"


# ──────────────────────────────────────────────────────────────────────────────
# CUSTOM SIGNAL-PLOT WIDGET
# ──────────────────────────────────────────────────────────────────────────────

class _SigPlotWidget(pg.PlotWidget):
    def __init__(self, win_ref, **kwargs):
        super().__init__(**kwargs)
        self._win = win_ref

    def wheelEvent(self, ev):
        pos = self.plotItem.vb.mapSceneToView(
            QtCore.QPointF(ev.pos().x(), ev.pos().y())
        )
        ri = N_ROWS - 1 - int(round(pos.y()))
        ri = max(0, min(N_ROWS - 1, ri))
        row = DISPLAY_ROWS[ri]
        if ev.angleDelta().y() > 0:
            STATE.row_scales[row] /= ZOOM_FACTOR
        else:
            STATE.row_scales[row] *= ZOOM_FACTOR
        STATE.selected_row = ri
        self._win._refresh_yticks()
        ev.accept()

    def leaveEvent(self, ev):
        self._win._cursor_line.setVisible(False)
        self._win._cursor_label.setVisible(False)
        super().leaveEvent(ev)


# ──────────────────────────────────────────────────────────────────────────────
# COLORED AXIS ITEM
# ──────────────────────────────────────────────────────────────────────────────

class _ColoredAxisItem(pg.AxisItem):
    def drawPicture(self, p, axisSpec, tickSpecs, textSpecs):
        p.setRenderHint(p.Antialiasing, False)
        p.setRenderHint(p.TextAntialiasing, True)

        pen, p1, p2 = axisSpec
        p.setPen(pen)
        p.drawLine(p1, p2)
        p.translate(0.5, 0)

        for t_pen, t_p1, t_p2 in tickSpecs:
            p.setPen(t_pen)
            p.drawLine(t_p1, t_p2)

        if self.style.get('tickFont') is not None:
            p.setFont(self.style['tickFont'])
        for rect, flags, text in textSpecs:
            col = next(
                (ROW_COLORS[r] for r in ROW_COLORS if text.startswith(r)),
                "#aaaaaa")
            p.setPen(pg.mkPen(col))
            p.drawText(rect, flags, text)


# ──────────────────────────────────────────────────────────────────────────────
# MAIN WINDOW
# ──────────────────────────────────────────────────────────────────────────────

class EEGWindow(QtWidgets.QMainWindow):
    def __init__(self, proc: RawChannelProcessor):
        super().__init__()
        self.proc = proc
        self.setWindowTitle("EEG Wearable  ·  Raw Channels (vs SRB1)")
        self.setStyleSheet("background-color: #0a0a0a;")
        self._build_ui()
        self._wire_callbacks()

        PERF_EVERY = 200
        _pt = {"n": 0, "drain": 0.0, "filter": 0.0, "array": 0.0,
               "setdata": 0.0, "misc": 0.0, "total": 0.0, "interval": 0.0}
        _last_t = [0.0]
        _fft_counter = [0]
        _hr_counter  = [0]
        FFT_EVERY = 6
        HR_EVERY  = 25

        # Rail detection: each raw channel maps 1-to-1 to its ADS1299 channel index
        _ROW_RAIL_CH = {row: [i] for i, row in enumerate(RAW_CH_LABELS)}
        _ROW_TO_CH   = {row: i  for i, row in enumerate(RAW_CH_LABELS)}

        _last_pen_col = {}
        _last_vis     = {}
        _last_bias_col  = [None]
        _last_bat_state = [None]

        _was_streaming = [False]

        def _update():
            try:
                _update_inner()
            except Exception:
                import traceback
                traceback.print_exc()

        def _update_inner():
            _t0 = time.perf_counter()
            if _last_t[0]:
                _pt["interval"] += _t0 - _last_t[0]
            _last_t[0] = _t0

            # 0. Battery / PMIC
            with STATE.lock:
                _pct_now      = STATE.vbat_pct
                _mv_now       = STATE.vbat_mv
                _charging_now = STATE.pmic_charging
                _err_now      = STATE.pmic_error
            if _mv_now > 0:
                self._bat_bar.setValue(_pct_now)
                self._lbl_bat_pct.setText(f"{_pct_now}%")
                self._lbl_bat_mv.setText(f"{_mv_now/1000:.2f}V")
            if _err_now:
                _bc, _sc, _st = "#ef5350", "#ef5350", "FAULT"
            elif _charging_now:
                _bc, _sc, _st = "#4caf50", "#4caf50", "CHG"
            elif _mv_now >= 4100:
                _bc, _sc, _st = "#ffffff", "#ffffff", "FULL"
            elif _pct_now < 20 and _mv_now > 0:
                _bc, _sc, _st = "#ef5350", "#ef5350", "LOW"
            elif _mv_now > 0:
                _bc, _sc, _st = "#ffffff", "#ffffff", "OK"
            else:
                _bc, _sc, _st = "#444444", "#555555", "—"
            if _mv_now > 0:
                self._bat_bar.setStyleSheet(f"""
                    QProgressBar {{
                        background: #2a2a2a; border: none; border-radius: 3px;
                    }}
                    QProgressBar::chunk {{
                        background: {_bc}; border-radius: 3px;
                    }}
                """)
                self._lbl_bat_pct.setStyleSheet(
                    f"color:{_bc};font-size:8pt;font-family:monospace;font-weight:bold;")
            self._lbl_bat_status.setText(_st)
            self._lbl_bat_status.setStyleSheet(
                f"color:{_sc};font-size:7pt;font-family:monospace;")

            # 0b. IMU overlay
            with STATE.lock:
                _imu_x      = STATE.imu_x
                _imu_y      = STATE.imu_y
                _imu_z      = STATE.imu_z
                _mot_active = STATE.motion_active
                _imu_temp   = STATE.imu_temp
            self._lbl_imu_x.setText(f"X: {_imu_x:+5d} mg")
            self._lbl_imu_y.setText(f"Y: {_imu_y:+5d} mg")
            self._lbl_imu_z.setText(f"Z: {_imu_z:+5d} mg")
            self._lbl_imu_temp.setText(f"T: {_imu_temp/100:.1f} °C")
            self._lbl_motion.setText("● MOTION" if _mot_active else "")

            # 1. Drain pending BLE batches
            with STATE.lock:
                if not STATE.pending:
                    return
                batches = list(STATE.pending)
                STATE.pending.clear()
            _t1 = time.perf_counter()

            batch_np = np.vstack(batches)   # (N, 8)
            if _pt["n"] < 3:
                print(f"[DBG] _update draining {batch_np.shape[0]} samples "
                      f"({len(batches)} pkts)  shape={batch_np.shape}")

            # 2. Extract raw channels (CH1–CH7) — no derivation, just slice
            raw_7ch = batch_np[:, :N_RAW]   # (N, 7)  each channel vs SRB1 in hardware

            if STATE.hw_test_mode:
                # In HW test mode all channels get the same square wave from the ADS1299.
                # Show them unfiltered so the sharp edges remain visible.
                filt_7ch = raw_7ch.copy()
            else:
                filt_7ch = proc.process(raw_7ch)

            bias_new = batch_np[:, 7]   # CH8 BIASOUT_DRN

            with STATE.lock:
                STATE.filt_buf.push(filt_7ch)
                STATE.raw_d_buf.push(raw_7ch)
                STATE.bias_buf.push(bias_new)
            _t2 = time.perf_counter()

            # 3. Build display slices
            ws = STATE.window_samples
            with STATE.lock:
                n_disp    = min(ws, len(STATE.filt_buf))
                filt_disp = STATE.filt_buf.last(n_disp)
                raw_disp  = STATE.raw_d_buf.last(n_disp)
                bias_disp = STATE.bias_buf.last(n_disp)
                cur_rails = STATE.rails.copy()
                STATE.rails[:] = False
            _t3 = time.perf_counter()

            if n_disp == 0:
                return

            step = max(1, n_disp // 2500)
            t_ax      = np.linspace(0, ws / FS, n_disp)[::step]
            filt_disp = filt_disp[::step]
            raw_disp  = raw_disp[::step]
            bias_disp = bias_disp[::step]
            raw_disp  = raw_disp - raw_disp.mean(axis=0)

            # 4. Update signal curves
            show_raw = STATE.show_raw
            for row_i, row in enumerate(DISPLAY_ROWS):
                y_off = float(N_ROWS - 1 - row_i)
                scale = max(STATE.row_scales.get(row, 100.0), 1.0)
                if row == "BIAS":
                    self._curves_bias.setData(t_ax, bias_disp / scale + y_off)
                else:
                    ch       = _ROW_TO_CH[row]
                    railing  = any(cur_rails[c] for c in _ROW_RAIL_CH[row] if c < 8)
                    base_col = ROW_COLORS[row]
                    pen_col  = "#ef5350" if railing else base_col
                    cr = self._curves_raw[row]
                    cf = self._curves_filt[row]
                    cr_id = id(cr)
                    cf_id = id(cf)
                    if show_raw:
                        if _last_pen_col.get(cr_id) != pen_col:
                            cr.setPen(pg.mkPen(pen_col, width=1))
                            _last_pen_col[cr_id] = pen_col
                        cr.setData(t_ax, raw_disp[:, ch] / scale + y_off)
                        if not _last_vis.get(cr_id, False):
                            cr.setVisible(True); _last_vis[cr_id] = True
                        if _last_vis.get(cf_id, True):
                            cf.setVisible(False); _last_vis[cf_id] = False
                    else:
                        if _last_vis.get(cr_id, False):
                            cr.setVisible(False); _last_vis[cr_id] = False
                        if not _last_vis.get(cf_id, False):
                            cf.setVisible(True); _last_vis[cf_id] = True
                        if _last_pen_col.get(cf_id) != pen_col:
                            cf.setPen(pg.mkPen(pen_col, width=1))
                            _last_pen_col[cf_id] = pen_col
                        cf.setData(t_ax, filt_disp[:, ch] / scale + y_off)
            _t4 = time.perf_counter()

            # 5. BIAS status
            if len(bias_disp) > 0:
                bias_rms = float(np.sqrt(np.mean(
                    bias_disp[-min(50, len(bias_disp)):]**2)))
                frac = min(bias_rms / 4_500_000.0, 1.0)
                if frac < 0.10:
                    bc, bt = "#4caf50", "BIAS OK"
                elif frac < 0.40:
                    bc, bt = "#ffa726", "BIAS HIGH"
                else:
                    bc, bt = "#ef5350", "BIAS RAIL"
                self._lbl_drl.setText(bt)
                self._lbl_drl.setStyleSheet(_LABEL_STYLE.format(fg=bc))

            # 6. Connection status + filter reset on reconnect
            is_streaming = BLE_CLIENT.connected
            if is_streaming and not _was_streaming[0]:
                proc.reset_state()
                print("[BLE] New connection detected — filter state reset.")
                with STATE.lock:
                    gap_start = STATE.ble_gap_start
                    recording = STATE.recording
                    STATE.ble_gap_start = None
                if gap_start is not None and recording:
                    gap_s = time.time() - gap_start
                    n_eeg = int(gap_s * FS)
                    n_imu = int(gap_s * IMU_FS)
                    zero_row = np.zeros(8, dtype=np.float64)
                    with STATE.lock:
                        for _ in range(n_eeg):
                            STATE.rec_buf.append(zero_row)
                        for _ in range(n_imu):
                            STATE.imu_rec_buf.append((0, 0, 0, 0, 0))
                    print(f"[REC] BLE gap {gap_s:.1f} s — zero-filled "
                          f"{n_eeg} EEG + {n_imu} IMU samples")
            elif not is_streaming and _was_streaming[0]:
                with STATE.lock:
                    if STATE.recording:
                        STATE.ble_gap_start = time.time()
                        print("[REC] BLE disconnected during recording — gap timer started")

            _was_streaming[0] = is_streaming

            if TEST_MODE:
                self._lbl_conn.setText("TEST")
            elif is_streaming:
                self._lbl_conn.setText("LIVE")
                self._lbl_conn.setStyleSheet(_LABEL_STYLE.format(fg="#4caf50"))
            else:
                self._lbl_conn.setText("Scanning…")
                self._lbl_conn.setStyleSheet(_LABEL_STYLE.format(fg="#ffa726"))

            # 7. Heart rate (uses CH2 / index 1 as ECG proxy)
            _hr_counter[0] += 1
            if _hr_counter[0] >= HR_EVERY:
                _hr_counter[0] = 0
                n_ecg = min(int(FS * 10), len(STATE.filt_buf))
                if n_ecg >= int(FS * 2):
                    with STATE.lock:
                        ecg_sig = STATE.filt_buf.last(n_ecg)[:, 1]
                    bpm = estimate_hr_live(ecg_sig, FS)
                    if bpm is not None:
                        self._lbl_bpm.setText(f"{bpm:.0f}")

            # 8. FFT (CH4/5/6 = EEG channels, indices 3/4/5)
            if STATE.fft_live:
                _fft_counter[0] += 1
                if _fft_counter[0] >= FFT_EVERY:
                    _fft_counter[0] = 0
                    n_fft = min(4 * FS, len(STATE.filt_buf))
                    if n_fft >= 64:
                        with STATE.lock:
                            fft_data = STATE.filt_buf.last(n_fft)
                        eeg_fft_rows = ["CH4 EEG_L1", "CH5 EEG_L2", "CH6 EEG_L3"]
                        all_psd = []
                        for row in eeg_fft_rows:
                            ci = RAW_CH_LABELS.index(row)
                            f, pxx = welch(fft_data[:, ci], fs=FS,
                                           nperseg=min(256, n_fft),
                                           noverlap=min(128, n_fft // 2))
                            mask    = f <= 50
                            pxx_db  = np.log10(np.maximum(pxx[mask], 1e-10))
                            self._curves_fft[row].setData(f[mask], pxx_db)
                            all_psd.append(pxx[mask])
                        if all_psd:
                            mean_p = np.mean(all_psd, axis=0)
                            self._curve_fft_mean.setData(
                                f[mask],
                                np.log10(np.maximum(mean_p, 1e-10)))

            # PERF
            _t5 = time.perf_counter()
            _pt["n"]       += 1
            _pt["drain"]   += _t1 - _t0
            _pt["filter"]  += _t2 - _t1
            _pt["array"]   += _t3 - _t2
            _pt["setdata"] += _t4 - _t3
            _pt["misc"]    += _t5 - _t4
            _pt["total"]   += _t5 - _t0
            if _pt["n"] % PERF_EVERY == 0:
                n   = _pt["n"]
                fps = n / _pt["interval"] if _pt["interval"] > 0 else 0
                print(
                    f"[PERF] {n} frames | actual={fps:.1f}fps  "
                    f"py_total={1000*_pt['total']/n:.1f}ms  "
                    f"(drain={1000*_pt['drain']/n:.2f} "
                    f"filter={1000*_pt['filter']/n:.2f} "
                    f"array={1000*_pt['array']/n:.2f} "
                    f"setdata={1000*_pt['setdata']/n:.2f} "
                    f"misc={1000*_pt['misc']/n:.2f})"
                )

        self._timer = QtCore.QTimer()
        self._timer.setInterval(16)
        self._timer.timeout.connect(_update)
        self._timer.start()

        self._flush_timer = QtCore.QTimer()
        self._flush_timer.setInterval(EDF_FLUSH_INTERVAL_S * 1000)
        self._flush_timer.timeout.connect(self._periodic_edf_flush)
        self._flush_timer.start()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        central = QtWidgets.QWidget()
        central.setStyleSheet("background-color: #0a0a0a;")
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setSpacing(4)
        root.setContentsMargins(6, 4, 6, 4)

        # ── Top button bar ────────────────────────────────────────────────
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(4)

        self._win_btns = []
        for i, lbl in enumerate(WINDOW_LABELS):
            bg = "#2e4057" if i == DEFAULT_WIN else "#1a1a1a"
            b = _btn(lbl, bg=bg)
            b.setFixedWidth(52)
            self._win_btns.append(b)
            top.addWidget(b)

        top.addSpacing(16)
        self._btn_raw    = _btn("raw: off",   fg="#666666")
        self._btn_notch  = _btn("60Hz: on",   fg="#66bb6a")
        self._btn_band   = _btn("Full",        fg="#ab47bc")
        self._btn_fft    = _btn("PSD: off",   fg="#666666")
        self._btn_ascale = _btn("auto-scale", fg="#ffd54f")
        self._btn_hwtest = _btn("test: off",  fg="#555555")
        self._btn_drl    = _btn("DRL: on",    fg="#66bb6a")
        for b in (self._btn_raw, self._btn_notch, self._btn_band,
                  self._btn_fft, self._btn_ascale, self._btn_hwtest,
                  self._btn_drl):
            b.setFixedWidth(80)
            top.addWidget(b)
        top.addStretch()

        root.addLayout(top)

        # ── Signal plot ───────────────────────────────────────────────────
        self._plot_sig = _SigPlotWidget(
            self,
            background="#111111",
            axisItems={'left': _ColoredAxisItem(orientation='left')},
        )
        pi = self._plot_sig.plotItem
        pi.setMouseEnabled(x=False, y=False)
        pi.showGrid(x=True, alpha=0.15)
        pi.setYRange(-0.55, N_ROWS - 0.45, padding=0)
        pi.setXRange(0, WINDOW_SEC[DEFAULT_WIN], padding=0)
        pi.getAxis("bottom").setLabel("Time (s)", color="#666666")
        pi.getAxis("bottom").setTextPen(pg.mkPen("#666666"))
        pi.getAxis("left").setTextPen(pg.mkPen("#aaaaaa"))
        pi.getAxis("left").setWidth(165)

        self._curves_filt = {}
        self._curves_raw  = {}
        self._curves_bias = None

        for row_i, row in enumerate(DISPLAY_ROWS):
            c     = ROW_COLORS[row]
            y_off = float(N_ROWS - 1 - row_i)
            if row == "BIAS":
                cur = pg.PlotCurveItem(
                    pen=pg.mkPen(c, width=1, style=QtCore.Qt.DashLine))
                self._plot_sig.addItem(cur)
                self._curves_bias = cur
            else:
                cr = pg.PlotCurveItem(pen=pg.mkPen(c, width=1))
                cr.setVisible(False)
                self._plot_sig.addItem(cr)
                self._curves_raw[row] = cr
                cf = pg.PlotCurveItem(pen=pg.mkPen(c, width=1))
                self._plot_sig.addItem(cf)
                self._curves_filt[row] = cf

        self._refresh_yticks()

        self._test_overlay = pg.TextItem(
            "TEST MODE  —  ~1 Hz internal square wave  ±1.875 mV",
            color=(255, 167, 38),
            anchor=(0.0, 1.0))
        self._test_overlay.setPos(0.05, N_ROWS - 0.1)
        self._test_overlay.setFont(QtGui.QFont("monospace", 9))
        self._plot_sig.addItem(self._test_overlay)
        self._test_overlay.setVisible(False)

        self._cursor_line = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen("#888888", width=1, style=QtCore.Qt.DashLine))
        self._cursor_line.setVisible(False)
        self._plot_sig.addItem(self._cursor_line)

        self._cursor_label = pg.TextItem(
            text="", color="#dddddd", anchor=(0.0, 0.5),
            fill=pg.mkBrush(0, 0, 0, 160))
        self._cursor_label.setFont(QtGui.QFont("monospace", 8))
        self._cursor_label.setVisible(False)
        self._plot_sig.addItem(self._cursor_label)

        root.addWidget(self._plot_sig, stretch=65)

        # ── FFT plot ──────────────────────────────────────────────────────
        self._plot_fft = pg.PlotWidget(background="#111111")
        pf = self._plot_fft.plotItem
        pf.setMouseEnabled(x=False, y=False)
        pf.showGrid(x=True, y=True, alpha=0.12)
        pf.setXRange(0, 50, padding=0)
        pf.setYRange(np.log10(0.01), np.log10(1e5), padding=0)
        pf.getAxis("bottom").setLabel("Frequency (Hz)", color="#666666")
        pf.getAxis("bottom").setTextPen(pg.mkPen("#666666"))
        pf.getAxis("left").setLabel("log PSD", color="#666666")
        pf.getAxis("left").setTextPen(pg.mkPen("#666666"))

        for f0, f1, col in [
            (0.5,  4,  "#1565c0"), (4,   8,  "#00695c"),
            (8,   13,  "#1b5e20"), (13, 30,  "#e65100"),
            (30,  50,  "#b71c1c"),
        ]:
            region = pg.LinearRegionItem(
                [f0, f1], orientation="vertical",
                brush=pg.mkBrush(col + "18"), movable=False,
                pen=pg.mkPen(None))
            self._plot_fft.addItem(region)

        eeg_fft_rows = ["CH4 EEG_L1", "CH5 EEG_L2", "CH6 EEG_L3"]
        self._curves_fft = {}
        for row in eeg_fft_rows:
            cur = pg.PlotCurveItem(
                pen=pg.mkPen(ROW_COLORS[row], width=1), alpha=0.85)
            self._plot_fft.addItem(cur)
            self._curves_fft[row] = cur
        self._curve_fft_mean = pg.PlotCurveItem(
            pen=pg.mkPen("w", width=1.5, style=QtCore.Qt.DashLine))
        self._plot_fft.addItem(self._curve_fft_mean)
        self._plot_fft.setVisible(False)

        root.addWidget(self._plot_fft, stretch=30)

        # ── Bottom control bar ────────────────────────────────────────────
        bot = QtWidgets.QHBoxLayout()
        bot.setSpacing(6)

        self._gain_sliders  = []
        self._gain_val_lbls = []
        for i, lbl_text in enumerate(GAIN_SLIDER_LABELS):
            grp = QtWidgets.QWidget()
            grp.setStyleSheet("background-color: #0a0a0a;")
            vl = QtWidgets.QVBoxLayout(grp)
            vl.setSpacing(1)
            vl.setContentsMargins(2, 2, 2, 2)

            lbl = QtWidgets.QLabel(lbl_text)
            lbl.setStyleSheet("color: #aaaaaa; font-size: 7pt; font-family: monospace;")
            lbl.setAlignment(QtCore.Qt.AlignCenter)

            sl = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            sl.setMinimum(0)
            sl.setMaximum(len(GAIN_VALUES) - 1)
            sl.setValue(GAIN_VALUES.index(STATE.gain[i]))
            sl.setFixedHeight(18)
            sl.setStyleSheet("""
                QSlider::groove:horizontal {
                    height: 4px; background: #2a2a2a; border-radius: 2px;
                }
                QSlider::handle:horizontal {
                    background: #2e4057; width: 10px; height: 10px;
                    margin: -3px 0; border-radius: 5px;
                }
            """)

            val_lbl = QtWidgets.QLabel(f"x{STATE.gain[i]}")
            val_lbl.setStyleSheet(
                "color: #4fc3f7; font-size: 7pt; font-family: monospace;")
            val_lbl.setAlignment(QtCore.Qt.AlignCenter)

            vl.addWidget(lbl)
            vl.addWidget(sl)
            vl.addWidget(val_lbl)
            bot.addWidget(grp, stretch=2)

            self._gain_sliders.append(sl)
            self._gain_val_lbls.append(val_lbl)

        bot.addSpacing(8)

        # HR box
        hr_box = QtWidgets.QWidget()
        hr_box.setStyleSheet("background-color: #111111; border-radius: 3px;")
        hr_vl = QtWidgets.QVBoxLayout(hr_box)
        hr_vl.setSpacing(0)
        hr_vl.setContentsMargins(4, 3, 4, 3)
        hr_vl.addWidget(QtWidgets.QLabel(
            "CH2 HR", styleSheet="color:#555555;font-size:6pt;font-family:monospace;"))
        self._lbl_bpm = QtWidgets.QLabel("—")
        self._lbl_bpm.setStyleSheet(
            "color:#ef5350;font-size:14pt;font-family:monospace;font-weight:bold;")
        self._lbl_bpm.setAlignment(QtCore.Qt.AlignCenter)
        hr_vl.addWidget(self._lbl_bpm)
        hr_vl.addWidget(QtWidgets.QLabel(
            "bpm", styleSheet="color:#555555;font-size:6pt;font-family:monospace;"))
        bot.addWidget(hr_box)

        # Status box
        st_box = QtWidgets.QWidget()
        st_box.setStyleSheet("background-color: #111111; border-radius: 3px;")
        st_vl = QtWidgets.QVBoxLayout(st_box)
        st_vl.setSpacing(2)
        st_vl.setContentsMargins(4, 3, 4, 3)
        self._lbl_drl = QtWidgets.QLabel("BIAS —")
        self._lbl_drl.setStyleSheet(
            "color:#4caf50;font-size:8pt;font-family:monospace;")
        self._lbl_drl.setAlignment(QtCore.Qt.AlignCenter)
        self._lbl_conn = QtWidgets.QLabel(
            "TEST" if TEST_MODE else "Scanning…")
        self._lbl_conn.setStyleSheet(
            "color:#555555;font-size:8pt;font-family:monospace;")
        self._lbl_conn.setAlignment(QtCore.Qt.AlignCenter)
        st_vl.addWidget(self._lbl_drl)
        st_vl.addWidget(self._lbl_conn)
        bot.addWidget(st_box)

        bot.addSpacing(8)

        # IMU / motion box
        imu_box = QtWidgets.QWidget()
        imu_box.setStyleSheet("background-color: #111111; border-radius: 3px;")
        imu_vl = QtWidgets.QVBoxLayout(imu_box)
        imu_vl.setSpacing(0)
        imu_vl.setContentsMargins(6, 3, 6, 3)
        imu_hdr = QtWidgets.QHBoxLayout()
        imu_hdr.setSpacing(4)
        imu_hdr.addWidget(QtWidgets.QLabel(
            "IMU", styleSheet="color:#555555;font-size:6pt;font-family:monospace;"))
        self._lbl_motion = QtWidgets.QLabel("")
        self._lbl_motion.setStyleSheet(
            "color:#ef5350;font-size:6pt;font-family:monospace;font-weight:bold;")
        imu_hdr.addWidget(self._lbl_motion)
        imu_hdr.addStretch()
        imu_vl.addLayout(imu_hdr)
        self._lbl_imu_x = QtWidgets.QLabel("X: — mg")
        self._lbl_imu_y = QtWidgets.QLabel("Y: — mg")
        self._lbl_imu_z = QtWidgets.QLabel("Z: — mg")
        self._lbl_imu_temp = QtWidgets.QLabel("T: —.— °C")
        for lbl in (self._lbl_imu_x, self._lbl_imu_y,
                    self._lbl_imu_z, self._lbl_imu_temp):
            lbl.setStyleSheet("color:#aaaaaa;font-size:7pt;font-family:monospace;")
        imu_vl.addWidget(self._lbl_imu_x)
        imu_vl.addWidget(self._lbl_imu_y)
        imu_vl.addWidget(self._lbl_imu_z)
        imu_vl.addWidget(self._lbl_imu_temp)
        bot.addWidget(imu_box)

        bot.addSpacing(8)

        # Battery / PMIC box
        bat_box = QtWidgets.QWidget()
        bat_box.setStyleSheet("background-color: #111111; border-radius: 3px;")
        bat_vl = QtWidgets.QVBoxLayout(bat_box)
        bat_vl.setSpacing(1)
        bat_vl.setContentsMargins(6, 3, 6, 3)

        bat_top = QtWidgets.QHBoxLayout()
        bat_top.setSpacing(4)
        bat_top.addWidget(QtWidgets.QLabel(
            "BATT", styleSheet="color:#555555;font-size:6pt;font-family:monospace;"))
        self._bat_bar = QtWidgets.QProgressBar()
        self._bat_bar.setRange(0, 100)
        self._bat_bar.setValue(0)
        self._bat_bar.setTextVisible(False)
        self._bat_bar.setFixedWidth(60)
        self._bat_bar.setFixedHeight(8)
        self._bat_bar.setStyleSheet("""
            QProgressBar {
                background: #2a2a2a; border: none; border-radius: 3px;
            }
            QProgressBar::chunk {
                background: #4caf50; border-radius: 3px;
            }
        """)
        bat_top.addWidget(self._bat_bar)
        self._lbl_bat_pct = QtWidgets.QLabel("—%")
        self._lbl_bat_pct.setStyleSheet(
            "color:#4caf50;font-size:8pt;font-family:monospace;font-weight:bold;")
        bat_top.addWidget(self._lbl_bat_pct)
        bat_vl.addLayout(bat_top)

        bat_bot = QtWidgets.QHBoxLayout()
        bat_bot.setSpacing(6)
        self._lbl_bat_mv = QtWidgets.QLabel("—.—V")
        self._lbl_bat_mv.setStyleSheet(
            "color:#888888;font-size:7pt;font-family:monospace;")
        bat_bot.addWidget(self._lbl_bat_mv)
        self._lbl_bat_status = QtWidgets.QLabel("—")
        self._lbl_bat_status.setStyleSheet(
            "color:#555555;font-size:7pt;font-family:monospace;")
        bat_bot.addWidget(self._lbl_bat_status)
        bat_vl.addLayout(bat_bot)
        bot.addWidget(bat_box)

        bot.addSpacing(8)

        # REC / Save buttons
        self._btn_rec  = _btn("REC",      fg="#ef5350")
        self._btn_save = _btn("Save EDF", fg="#555555")
        self._btn_rec.setFixedWidth(80)
        self._btn_save.setFixedWidth(90)
        bot.addWidget(self._btn_rec)
        bot.addWidget(self._btn_save)

        root.addLayout(bot)
        self.resize(1400, 900)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _wire_callbacks(self):
        pi = self._plot_sig.plotItem

        def _set_window(i):
            def cb():
                STATE.window_idx = i
                pi.setXRange(0, WINDOW_SEC[i], padding=0)
                for j, b in enumerate(self._win_btns):
                    bg = "#2e4057" if j == i else "#1a1a1a"
                    b.setStyleSheet(_BTN_STYLE.format(bg=bg, fg="#cccccc"))
            return cb
        for i, b in enumerate(self._win_btns):
            b.clicked.connect(_set_window(i))

        def toggle_raw():
            STATE.show_raw = not STATE.show_raw
            lbl = "raw: on" if STATE.show_raw else "raw: off"
            col = "#4fc3f7" if STATE.show_raw else "#666666"
            self._btn_raw.setText(lbl)
            self._btn_raw.setStyleSheet(_BTN_STYLE.format(bg="#1a1a1a", fg=col))
            # Re-scale to match the new mode — raw signals are much larger than filtered
            self._do_autoscale()
        self._btn_raw.clicked.connect(toggle_raw)
        self._toggle_raw = toggle_raw

        def toggle_notch():
            on = not self.proc.notch_on
            self.proc.set_notch(on)
            lbl = "60Hz: on" if on else "60Hz: off"
            col = "#66bb6a" if on else "#ef5350"
            self._btn_notch.setText(lbl)
            self._btn_notch.setStyleSheet(_BTN_STYLE.format(bg="#1a1a1a", fg=col))
        self._btn_notch.clicked.connect(toggle_notch)
        self._toggle_notch = toggle_notch

        _band_list = list(BAND_RANGES.keys())
        def cycle_band():
            idx = _band_list.index(self.proc.band_mode)
            new = _band_list[(idx + 1) % len(_band_list)]
            self.proc.set_band(new)
            STATE.band_mode = new
            self._btn_band.setText(new)
        self._btn_band.clicked.connect(cycle_band)
        self._cycle_band = cycle_band

        def toggle_fft():
            STATE.fft_live = not STATE.fft_live
            lbl = "PSD: on" if STATE.fft_live else "PSD: off"
            col = "#ffd54f" if STATE.fft_live else "#666666"
            self._btn_fft.setText(lbl)
            self._btn_fft.setStyleSheet(_BTN_STYLE.format(bg="#1a1a1a", fg=col))
            self._plot_fft.setVisible(STATE.fft_live)
            if not STATE.fft_live:
                for c in self._curves_fft.values(): c.setData([], [])
                self._curve_fft_mean.setData([], [])
        self._btn_fft.clicked.connect(toggle_fft)
        self._toggle_fft = toggle_fft

        def make_gain_cb(idx):
            def cb(val):
                g = GAIN_VALUES[int(val)]
                STATE.gain[idx] = g
                self._gain_val_lbls[idx].setText(f"x{g}")
                for row in _GAIN_ROWS[idx]:
                    STATE.row_scales[row] = DEFAULT_SCALE_UV[row] * (24.0 / g)
                self._refresh_yticks()
                self.proc.reset_state()
                if not TEST_MODE: BLE_CLIENT.send_gain(idx, g)
            return cb
        for i, sl in enumerate(self._gain_sliders):
            sl.valueChanged.connect(make_gain_cb(i))

        def do_autoscale():
            with STATE.lock:
                use_raw = STATE.show_raw
                buf = STATE.raw_d_buf if use_raw else STATE.filt_buf
                n = min(STATE.window_samples, len(buf))
                if n < 8:
                    return
                disp = buf.last(n)   # (N, 7)
                if use_raw:
                    disp = disp - disp.mean(axis=0)   # match DC removal in display
                bias = STATE.bias_buf.last(n)
            for row_i, row in enumerate(DISPLAY_ROWS):
                if row == "BIAS":
                    sig = bias
                else:
                    ci  = RAW_CH_LABELS.index(row)
                    sig = disp[:, ci]
                p95 = float(np.percentile(np.abs(sig), 95))
                STATE.row_scales[row] = max(p95 * 1.2, 1.0)
            self._refresh_yticks()
        self._btn_ascale.clicked.connect(do_autoscale)
        self._do_autoscale = do_autoscale

        def toggle_hwtest():
            STATE.hw_test_mode = not STATE.hw_test_mode
            on = STATE.hw_test_mode
            self._btn_hwtest.setText("test: ON" if on else "test: off")
            self._btn_hwtest.setStyleSheet(
                _BTN_STYLE.format(bg="#1a1a1a", fg="#ffa726" if on else "#555555"))
            self._test_overlay.setVisible(on)
            if not TEST_MODE:
                BLE_CLIENT.send_test_mode(on)
        self._btn_hwtest.clicked.connect(toggle_hwtest)
        self._toggle_hwtest = toggle_hwtest

        _gains_before_drl_off = [None, None, None, None]

        def toggle_drl():
            STATE.drl_active = not STATE.drl_active
            on = STATE.drl_active
            self._btn_drl.setText("DRL: on" if on else "DRL: off")
            self._btn_drl.setStyleSheet(
                _BTN_STYLE.format(bg="#1a1a1a", fg="#66bb6a" if on else "#ef5350"))
            if not on:
                for gi in range(4):
                    _gains_before_drl_off[gi] = STATE.gain[gi]
                    self._gain_sliders[gi].setValue(GAIN_VALUES.index(1))
            else:
                if _gains_before_drl_off[0] is not None:
                    for gi in range(4):
                        self._gain_sliders[gi].setValue(
                            GAIN_VALUES.index(_gains_before_drl_off[gi]))
                    for gi in range(4):
                        _gains_before_drl_off[gi] = None
            self.proc.reset_state()
            if not TEST_MODE:
                BLE_CLIENT.send_drl(on)
        self._btn_drl.clicked.connect(toggle_drl)
        self._toggle_drl = toggle_drl

        def toggle_rec():
            if not STATE.recording:
                STATE.recording   = True
                STATE.rec_start   = time.time()
                STATE.rec_buf     = []
                STATE.imu_rec_buf = []
                if STATE.edf_writer is not None:
                    try: STATE.edf_writer.close()
                    except Exception: pass
                writer, path, ts = _edf_open(STATE.rec_start, list(STATE.gain))
                STATE.edf_writer = writer
                STATE.edf_path   = path
                STATE.edf_ts     = ts
                self._btn_rec.setText("STOP")
                self._btn_rec.setStyleSheet(
                    _BTN_STYLE.format(bg="#1a1a1a", fg="#ffa726"))
                self._btn_save.setText("Save EDF")
                self._btn_save.setStyleSheet(
                    _BTN_STYLE.format(bg="#1a1a1a", fg="#555555"))
            else:
                STATE.recording = False
                self._btn_rec.setText("REC")
                self._btn_rec.setStyleSheet(
                    _BTN_STYLE.format(bg="#1a1a1a", fg="#ef5350"))
                self._btn_save.setStyleSheet(
                    _BTN_STYLE.format(bg="#1a1a1a", fg="#4fc3f7"))
        self._btn_rec.clicked.connect(toggle_rec)
        self._toggle_rec = toggle_rec

        def do_save():
            if STATE.recording:
                STATE.recording = False
                self._btn_rec.setText("REC")
                self._btn_rec.setStyleSheet(
                    _BTN_STYLE.format(bg="#1a1a1a", fg="#ef5350"))
            writer    = STATE.edf_writer
            path      = STATE.edf_path
            ts        = STATE.edf_ts
            rec_start = STATE.rec_start
            STATE.edf_writer = None
            STATE.edf_path   = None
            STATE.edf_ts     = None
            if writer is None and not STATE.rec_buf:
                print("[SAVE] Nothing recorded yet."); return
            if writer is not None:
                _edf_close(writer, path, ts, rec_start)
                STATE.rec_buf     = []
                STATE.imu_rec_buf = []
                self._btn_save.setText("Saved")
                self._btn_save.setStyleSheet(
                    _BTN_STYLE.format(bg="#1a1a1a", fg="#333333"))
        self._btn_save.clicked.connect(do_save)
        self._do_save = do_save

        self._cursor_proxy = pg.SignalProxy(
            self._plot_sig.scene().sigMouseMoved,
            rateLimit=60,
            slot=lambda ev: self._on_mouse_moved(ev[0]))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _periodic_edf_flush(self):
        with STATE.lock:
            writer = STATE.edf_writer
            if writer is None or not STATE.recording:
                return
            n_eeg = len(STATE.rec_buf)
            n_imu = len(STATE.imu_rec_buf)

        n_secs = min(n_eeg // FS, n_imu // IMU_FS) if n_imu > 0 else n_eeg // FS
        if n_secs == 0:
            return

        _edf_flush_to_disk(writer, n_secs * FS, n_secs * IMU_FS)

    def _refresh_yticks(self):
        ticks = []
        for i, row in enumerate(DISPLAY_ROWS):
            y  = float(N_ROWS - 1 - i)
            sc = STATE.row_scales.get(row, 100.0)
            ticks.append((y, f"{row}  ±{_fmt_scale(sc)}"))
        self._plot_sig.plotItem.getAxis("left").setTicks([ticks])

    def _on_mouse_moved(self, pos):
        if not self._plot_sig.sceneBoundingRect().contains(pos):
            self._cursor_line.setVisible(False)
            self._cursor_label.setVisible(False)
            return

        mp = self._plot_sig.plotItem.vb.mapSceneToView(pos)
        x  = mp.x()
        y  = mp.y()

        ws = WINDOW_SEC[STATE.window_idx]
        if not (0 <= x <= ws):
            self._cursor_line.setVisible(False)
            self._cursor_label.setVisible(False)
            return

        self._cursor_line.setPos(x)
        self._cursor_line.setVisible(True)

        row_i = N_ROWS - 1 - int(round(y))
        row_i = max(0, min(N_ROWS - 1, row_i))
        row   = DISPLAY_ROWS[row_i]

        if row == "BIAS":
            curve = self._curves_bias
        elif STATE.show_raw:
            curve = self._curves_raw.get(row)
        else:
            curve = self._curves_filt.get(row)

        val_str = "—"
        if curve is not None and curve.isVisible():
            xd, yd = curve.getData()
            if xd is not None and len(xd) > 1:
                idx   = int(np.searchsorted(xd, x))
                idx   = max(0, min(idx, len(xd) - 1))
                y_off = float(N_ROWS - 1 - row_i)
                scale = max(STATE.row_scales.get(row, 100.0), 1.0)
                uv    = (yd[idx] - y_off) * scale
                val_str = ("−" if uv < 0 else "+") + _fmt_scale(abs(uv))

        self._cursor_label.setText(f"t={x:.3f}s   {row}  {val_str}")
        y_label = float(N_ROWS - 1 - row_i) + 0.35
        if x > 0.75 * ws:
            self._cursor_label.setAnchor((1.0, 0.5))
            self._cursor_label.setPos(x - 0.01 * ws, y_label)
        else:
            self._cursor_label.setAnchor((0.0, 0.5))
            self._cursor_label.setPos(x + 0.01 * ws, y_label)
        self._cursor_label.setVisible(True)

    # ── Keyboard ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, ev):
        k  = ev.text()
        pi = self._plot_sig.plotItem
        if k == "n":
            self._toggle_notch()
        elif k == "b":
            self._cycle_band()
        elif k == "u":
            self._toggle_raw()
        elif k == "f":
            self._toggle_fft()
        elif k == "w":
            self._win_btns[(STATE.window_idx + 1) % len(WINDOW_SEC)].click()
        elif k == "r":
            self._toggle_rec()
        elif k == "a":
            self._do_autoscale()
        elif k == "t":
            self._toggle_hwtest()
        elif k == "s":
            self._do_save()
        elif k in set("12345678"):
            STATE.selected_row = int(k) - 1
        elif k in ("+", "="):
            row = DISPLAY_ROWS[STATE.selected_row]
            STATE.row_scales[row] /= ZOOM_FACTOR
            self._refresh_yticks()
        elif k in ("-", "_"):
            row = DISPLAY_ROWS[STATE.selected_row]
            STATE.row_scales[row] *= ZOOM_FACTOR
            self._refresh_yticks()
        elif k == "q":
            self.close()
        else:
            super().keyPressEvent(ev)

    def closeEvent(self, ev):
        self._timer.stop()
        self._flush_timer.stop()
        writer = STATE.edf_writer
        if writer is not None:
            STATE.edf_writer = None
            print("[EDF] App closing with open recording — flushing and closing EDF…")
            _edf_close(writer, STATE.edf_path, STATE.edf_ts, STATE.rec_start)
        BLE_CLIENT.stop()
        print("Exited.")
        ev.accept()


# ──────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="EEG Raw Channel Viewer — each channel vs SRB1 (pyqtgraph)")
    parser.add_argument("--test", action="store_true",
                        help="Run with synthetic data (no hardware needed)")
    args = parser.parse_args()

    global TEST_MODE
    if args.test:
        TEST_MODE = True

    print("=" * 62)
    print("  EEG Raw Channel Viewer  [pyqtgraph]")
    print(f"  Mode   : {'TEST — synthetic data' if TEST_MODE else 'LIVE BLE'}")
    print(f"  FS     : {FS} Hz   |   Rows: {N_ROWS}  (CH1–CH7 vs SRB1, + BIAS)")
    print("  Keys   : n b u f w r s a t  |  1-8 select row  |  +/- zoom")
    print("           (a=auto-scale, t=HW test, 8=BIAS)")
    print("  Scroll : zoom row under cursor  |  q: quit")
    print("=" * 62)

    proc     = RawChannelProcessor(fs=FS)
    stop_evt = threading.Event()

    if TEST_MODE:
        t = threading.Thread(target=test_data_thread, args=(stop_evt,), daemon=True)
        t.start()
        print("[TEST] Synthetic generator started.")
    else:
        BLE_CLIENT.start()
        print(f"[BLE] Scanning for '{DEVICE_NAME}' …")

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = EEGWindow(proc)
    win.show()

    try:
        sys.exit(app.exec_())
    finally:
        stop_evt.set()


if __name__ == "__main__":
    main()
