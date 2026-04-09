#!/usr/bin/env python3
"""
eeg_stream_pg.py  —  Real-time EEG / EMG / ECG / EOG visualization (pyqtgraph)
================================================================================
Main visualizer for the nRF54L15 + ADS1299 EEG wearable. Uses pyqtgraph for
GPU-accelerated line rendering (~60 fps). Data pipeline: SharedState, _RingBuf,
EEGProcessor, BLE (bleak), EDF save (pyedflib).

Expected perf: ~60 fps.

Requirements:
    pip install pyqtgraph PyQt5 numpy scipy bleak pyEDFlib

Run:
    python eeg_stream_pg.py           # live BLE
    python eeg_stream_pg.py --test    # synthetic data (no hardware)

Keyboard shortcuts
------------------
  n           toggle 60 Hz notch on / off
  b           cycle band: Full → Delta → Theta → Alpha → Beta → Gamma → …
  u           toggle raw signal (raw = unfiltered at full opacity; default = filtered)
  f           toggle live PSD
  w           cycle window: 5 s → 10 s → 30 s → 1 min → …
  r           toggle Record / Stop
  s           Stop + Save EDF immediately
  1–7         select row for zoom  (1=EOG … 6=EEG_frontal, 7=REF)
  + / -       zoom in / out on selected row
  scroll      scroll wheel on signal panel zooms row under cursor
  q           quit
"""

import sys
import os
import argparse
import threading
import time
from collections import deque
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtWidgets, QtCore, QtGui
from scipy.signal import welch, find_peaks

from eeg_processing import (
    EEGProcessor, derive_signals,
    DERIVED_LABELS, DEFAULT_SCALE_UV,
)
from eeg_ble import BleEEGClient, DEVICE_NAME

# ── pyqtgraph global config ────────────────────────────────────────────────────
# antialias=False: skip sub-pixel AA on lines (not needed for dense EEG traces)
# useOpenGL=True:  GPU line rendering — this is the whole point
pg.setConfigOptions(antialias=False, useOpenGL=True)

# ──────────────────────────────────────────────────────────────────────────────
# TOP-LEVEL CONFIGURATION
# ──────────────────────────────────────────────────────────────────────────────

TEST_MODE = False
FS        = 250

MAX_DEQUE     = 15_000        # 60 s at 250 Hz
WINDOW_SEC    = [5, 10, 30, 60]
WINDOW_LABELS = ["5 s", "10 s", "30 s", "1 min"]
DEFAULT_WIN   = 1

DISPLAY_ROWS = DERIVED_LABELS + ["BIAS"]
N_ROWS       = len(DISPLAY_ROWS)

ROW_COLORS = {
    "EOG":          "#4fc3f7",
    "ECG":          "#ef5350",
    "EMG":          "#ffa726",
    "EEG occipital": "#66bb6a",
    "EEG central":  "#ab47bc",
    "EEG frontal":  "#26c6da",
    "BIAS":         "#78909c",
}

GAIN_VALUES       = [1, 2, 4, 6, 8, 12, 24]
GAIN_SLIDER_LABELS = ["CH1  EOG", "CH2/3  EMG·ECG", "CH4/6  EEG L1/L3", "CH5  EEG L2"]
_GAIN_ROWS = {
    0: ["EOG"],
    1: ["ECG", "EMG"],
    2: ["EEG occipital", "EEG frontal"],
    3: ["EEG central"],
}

ZOOM_FACTOR = 1.5


# ──────────────────────────────────────────────────────────────────────────────
# NUMPY RING BUFFER  (identical to eeg_stream.py)
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
            self._buf[h:]        = rows[:split]
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
# SHARED STATE  (identical to eeg_stream.py)
# ──────────────────────────────────────────────────────────────────────────────

class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.pending: deque = deque()
        self.filt_buf  = _RingBuf(MAX_DEQUE, 6)
        self.raw_d_buf = _RingBuf(MAX_DEQUE, 6)
        self.bias_buf  = _RingBuf(MAX_DEQUE)   # CH8 BIASOUT_DRN
        self.rails     = np.zeros(8, dtype=bool)  # per-channel rail flags (cleared each UI frame)
        self.window_idx   = DEFAULT_WIN
        self.show_raw     = False
        self.band_mode    = "Full"
        self.fft_live     = False
        self.selected_row = 0
        self.row_scales   = dict(DEFAULT_SCALE_UV)
        self.gain         = [24, 24, 24, 24]
        self.recording    = False
        self.rec_buf      = []
        self.rec_start    = None
        self.hw_test_mode = False   # True = ADS1299 internal square-wave test
        self.drl_active   = True    # True = DRL/BIAS circuit active (default)
        # PMIC / battery status (updated every ~5 s from firmware)
        self.vbat_mv      = 0
        self.vbat_pct     = 0
        self.pmic_charging = False
        self.pmic_error    = False

    @property
    def window_samples(self) -> int:
        return WINDOW_SEC[self.window_idx] * FS


STATE = SharedState()


# ──────────────────────────────────────────────────────────────────────────────
# SYNTHETIC TEST DATA  (identical to eeg_stream.py)
# ──────────────────────────────────────────────────────────────────────────────

def _pqrst(t: float) -> float:
    p =   60 * np.exp(-((t + 0.15)**2) / (2 * 0.025**2))
    q =  -50 * np.exp(-((t + 0.030)**2) / (2 * 0.010**2))
    r = 1500 * np.exp(-(  t**2)          / (2 * 0.012**2))
    s = -200 * np.exp(-((t - 0.030)**2) / (2 * 0.010**2))
    tw =  300 * np.exp(-((t - 0.160)**2) / (2 * 0.050**2))
    return p + q + r + s + tw


def test_data_thread(stop_evt: threading.Event):
    rng = np.random.default_rng(0)
    dt  = 1.0 / FS
    rr = 60.0 / 70.0; last_r = -rr; rr_jit = 0.04
    blink_due = 3.0; blink_phase = -1.0
    alpha_ph = [0.0, 2*np.pi/3, 4*np.pi/3]
    beta_ph  = [0.0, np.pi/4,   np.pi/2  ]
    ALPHA_W = 2 * np.pi * 10.0 * dt
    BETA_W  = 2 * np.pi * 18.0 * dt
    EOG_W   = 2 * np.pi *  0.3 * dt
    eog_ph  = 0.0; t = 0.0; BATCH = 8
    while not stop_evt.is_set():
        batch = []
        for _ in range(BATCH):
            eog = 50 * np.sin(eog_ph)
            if blink_phase < 0 and t >= blink_due:
                blink_phase = 0.0
                blink_due   = t + rng.uniform(3.0, 8.0)
            if blink_phase >= 0:
                eog += 700 * np.sin(np.pi * blink_phase)
                blink_phase += dt / 0.12
                if blink_phase >= 1.0: blink_phase = -1.0
            t_r = t - last_r - rr / 2.0
            ecg_val = _pqrst(t_r)
            if t - last_r >= rr:
                last_r = t
                rr = 60.0 / 70.0 + rng.uniform(-rr_jit, rr_jit)
            emg_far  = ecg_val + rng.normal(0, 25)
            emg_near = 0.85 * ecg_val + rng.normal(0, 90)
            eeg = []
            for i in range(3):
                sig  = 22 * np.sin(alpha_ph[i])
                sig += 5  * np.sin(beta_ph[i])
                sig += rng.normal(0, 10)
                eeg.append(sig)
                alpha_ph[i] = (alpha_ph[i] + ALPHA_W) % (2 * np.pi)
                beta_ph[i]  = (beta_ph[i]  + BETA_W ) % (2 * np.pi)
            ref  = 3 * np.sin(2 * np.pi * 0.05 * t) + rng.normal(0, 0.8)  # CH7 (unused/pwdn)
            bias = rng.normal(0, 4)   # CH8 BIASOUT_DRN synthetic noise
            eog_ph = (eog_ph + EOG_W) % (2 * np.pi)
            batch.append(np.array([eog, emg_far, emg_near,
                                    eeg[0], eeg[1], eeg[2],
                                    ref, bias], dtype=np.float64))
            t += dt
        with STATE.lock:
            STATE.pending.extend(batch)
            if STATE.recording:
                STATE.rec_buf.extend(batch)
        time.sleep(BATCH * dt * 0.92)


# ──────────────────────────────────────────────────────────────────────────────
# BLE CLIENT  (all BLE logic lives in eeg_ble.py)
# ──────────────────────────────────────────────────────────────────────────────

def _on_ble_sample(uv, gains, rails):
    """BleEEGClient sample callback — push data into shared state."""
    with STATE.lock:
        STATE.pending.append(uv)        # push entire (8,8) batch — one object vs 8
        STATE.rails |= rails
        if STATE.recording:
            STATE.rec_buf.extend(uv)    # uv is (8,8); extend yields rows


def _on_pmic(vbat_mv, pct, charging, error):
    """BleEEGClient PMIC callback — update battery status."""
    with STATE.lock:
        STATE.vbat_mv       = vbat_mv
        STATE.vbat_pct      = pct
        STATE.pmic_charging = charging
        STATE.pmic_error    = error


BLE_CLIENT = BleEEGClient()
BLE_CLIENT.set_sample_callback(_on_ble_sample)
BLE_CLIENT.set_pmic_callback(_on_pmic)


# ──────────────────────────────────────────────────────────────────────────────
# EDF SAVE  (identical to eeg_stream.py)
# ──────────────────────────────────────────────────────────────────────────────

def save_edf(buf: list, gain: int, rec_start: float = None):
    rec_dir = os.path.join(os.path.dirname(__file__), "recordings")
    os.makedirs(rec_dir, exist_ok=True)
    # Use rec_start for the filename so it matches the BCI event logs
    t0    = rec_start if rec_start else time.time()
    ts    = datetime.fromtimestamp(t0).strftime("%Y%m%d_%H%M%S")
    arr   = np.array(buf, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 8:
        print("[SAVE] Unexpected buffer shape — aborting."); return None
    try:
        import pyedflib
        fname = os.path.join(rec_dir, f"eeg_{ts}.edf")
        labels = ["EOG","EMG_far","EMG_near","EEG_L1","EEG_L2","EEG_L3","SRB1","DRL"]
        f = pyedflib.EdfWriter(fname, 8, file_type=pyedflib.FILETYPE_EDFPLUS)
        try:
            f.setStartdatetime(datetime.fromtimestamp(t0))
            for i in range(8):
                ch_max = round(max(float(np.max(np.abs(arr[:, i]))), 1.0), 1)
                f.setSignalHeader(i, {
                    "label": labels[i], "dimension": "uV",
                    "sample_frequency": FS,
                    "physical_max":  ch_max, "physical_min": -ch_max,
                    "digital_max": 32767,    "digital_min": -32768,
                    "prefilter": "HP:0.5Hz LP:40Hz N:60Hz",
                    "transducer": "ADS1299",
                })
            f.writeSamples([np.ascontiguousarray(arr[:, i]) for i in range(8)])
        finally:
            f.close()
    except ImportError:
        fname = os.path.join(rec_dir, f"eeg_{ts}.npy")
        np.save(fname, arr)
        print("[SAVE] pyedflib not found — saved as .npy")

    # Write sidecar JSON so BCI scripts can auto-sync without a manual keypress.
    # Fields: rec_start_epoch (float, time.time()), rec_start_iso, n_samples, fs, edf_file.
    import json
    meta = {
        "rec_start_epoch": t0,
        "rec_start_iso":   datetime.fromtimestamp(t0).isoformat(),
        "n_samples":       int(arr.shape[0]),
        "fs":              FS,
        "edf_file":        os.path.basename(fname),
    }
    meta_path = os.path.join(rec_dir, f"eeg_{ts}_meta.json")
    with open(meta_path, "w") as mf:
        json.dump(meta, mf, indent=2)

    print(f"[SAVE] {arr.shape[0]} samples ({arr.shape[0]/FS:.1f} s) -> {fname}")
    print(f"[SAVE] meta -> {meta_path}")
    return fname


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _hex_alpha(hex_color: str, alpha: float) -> tuple:
    """Return (R,G,B,A) tuple from hex colour string + 0-1 alpha."""
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
    """Format a ±scale value for the y-axis tick with 4 significant figures.
    Switches to mV once the value reaches 1000 µV.
    Examples: 100.0 µV, 1.240 mV, 12.40 mV, 124.0 mV
    """
    if sc_uv >= 1000.0:
        mv  = sc_uv / 1000.0
        dec = max(0, 3 - max(0, int(np.log10(mv))))
        return f"{mv:.{dec}f} mV"
    else:
        dec = max(0, 3 - max(0, int(np.log10(max(sc_uv, 1.0)))))
        return f"{sc_uv:.{dec}f} \u03bcV"


# ──────────────────────────────────────────────────────────────────────────────
# CUSTOM SIGNAL-PLOT WIDGET  (scroll-wheel row zoom)
# ──────────────────────────────────────────────────────────────────────────────

class _SigPlotWidget(pg.PlotWidget):
    """PlotWidget with per-row scroll-wheel zoom."""

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
        # Hide cursor when mouse exits the plot widget entirely
        self._win._cursor_line.setVisible(False)
        self._win._cursor_label.setVisible(False)
        super().leaveEvent(ev)


# ──────────────────────────────────────────────────────────────────────────────
# COLORED AXIS ITEM  (per-row label colors matching waveform)
# ──────────────────────────────────────────────────────────────────────────────

class _ColoredAxisItem(pg.AxisItem):
    """Left axis that colors each tick label to match its waveform row color."""

    def drawPicture(self, p, axisSpec, tickSpecs, textSpecs):
        p.setRenderHint(p.Antialiasing, False)
        p.setRenderHint(p.TextAntialiasing, True)

        # axis spine
        pen, p1, p2 = axisSpec
        p.setPen(pen)
        p.drawLine(p1, p2)
        p.translate(0.5, 0)   # pixel-alignment (matches pyqtgraph default)

        # tick marks
        for t_pen, t_p1, t_p2 in tickSpecs:
            p.setPen(t_pen)
            p.drawLine(t_p1, t_p2)

        # tick labels — color per row
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
    def __init__(self, proc: EEGProcessor):
        super().__init__()
        self.proc = proc
        self.setWindowTitle("EEG Wearable  ·  pyqtgraph")
        self.setStyleSheet("background-color: #0a0a0a;")
        self._build_ui()
        self._wire_callbacks()

        # ── Per-frame timing ─────────────────────────────────────────────
        PERF_EVERY = 200
        _pt = {"n": 0, "drain": 0.0, "filter": 0.0, "array": 0.0,
               "setdata": 0.0, "misc": 0.0, "total": 0.0, "interval": 0.0}
        _last_t = [0.0]
        _fft_counter  = [0]
        _hr_counter   = [0]
        FFT_EVERY = 6
        HR_EVERY  = 25

        # Pre-computed mappings — avoid repeated dict/index lookups inside hot loop
        _ROW_RAIL_CH = {
            "EOG": [0], "ECG": [1], "EMG": [1, 2],
            "EEG occipital": [3, 5], "EEG central": [4], "EEG frontal": [5],
        }
        _ROW_TO_CH = {row: DERIVED_LABELS.index(row) for row in DERIVED_LABELS}

        # Style-change caches — skip expensive Qt calls when value unchanged
        _last_pen_col = {}   # curve_item_id → last color string
        _last_vis     = {}   # curve_item_id → last bool
        _last_bias_col  = [None]
        _last_bat_state = [None]  # last (bar_color, status_text) tuple

        def _update():
            _t0 = time.perf_counter()
            if _last_t[0]:
                _pt["interval"] += _t0 - _last_t[0]
            _last_t[0] = _t0

            # 0. Battery / PMIC widget — runs every frame, independent of EEG data
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
            elif _pct_now < 40 and _mv_now > 0:
                _bc, _sc, _st = "#ffa726", "#ffa726", "OK"
            elif _mv_now > 0:
                _bc, _sc, _st = "#4caf50", "#4caf50", "OK"
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

            # 1. Drain — pending holds (8,8) arrays (one per BLE packet)
            with STATE.lock:
                if not STATE.pending:
                    return
                batches = list(STATE.pending)
                STATE.pending.clear()
            _t1 = time.perf_counter()

            batch_np = np.vstack(batches)   # (N,8) — faster than np.array on list of rows
            if _pt["n"] < 3:
                print(f"[DBG] _update draining {batch_np.shape[0]} samples "
                      f"({len(batches)} pkts)  shape={batch_np.shape}")

            # 2. Derive + filter (skip filter in HW test mode — keep square edges sharp)
            derived_raw = derive_signals(batch_np)
            if STATE.hw_test_mode:
                derived_filt = derived_raw.copy()
                # EMG (CH2−CH3) and EEG occipital (CH4−CH6) are bipolar channels.
                # In test mode all channels get the same square wave, so the bipolar
                # difference cancels to noise.  Substitute single channels so the
                # waveform is visible.
                derived_filt[:, DERIVED_LABELS.index("EMG")]          = batch_np[:, 1]
                derived_filt[:, DERIVED_LABELS.index("EEG occipital")] = batch_np[:, 3]
            else:
                derived_filt = proc.process(derived_raw)
            bias_new = batch_np[:, 7]   # CH8 = BIASOUT_DRN
            with STATE.lock:
                STATE.filt_buf.push(derived_filt)
                STATE.raw_d_buf.push(derived_raw)
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
                STATE.rails[:] = False   # clear after reading
            _t3 = time.perf_counter()

            if n_disp == 0:
                return

            step = max(1, n_disp // 2500)   # 2500 pts — higher resolution than 1200
            t_ax      = np.linspace(0, ws / FS, n_disp)[::step]
            filt_disp = filt_disp[::step]
            raw_disp  = raw_disp[::step]
            bias_disp = bias_disp[::step]

            # 4. Update signal curves + railing flash
            # cur_rails[0..5] maps to CH1..CH6; derived rows map CH→rail index
            # EOG=CH1(0), ECG=CH2(1), EMG=CH2-CH3(1 or 2), EEG rows=CH4-6(3-5)
            show_raw = STATE.show_raw   # local copy — avoids repeated attr lookup
            for row_i, row in enumerate(DISPLAY_ROWS):
                y_off  = float(N_ROWS - 1 - row_i)
                scale  = max(STATE.row_scales.get(row, 100.0), 1.0)
                if row == "BIAS":
                    self._curves_bias.setData(t_ax, bias_disp / scale + y_off)
                else:
                    ch = _ROW_TO_CH[row]
                    rail_chs = _ROW_RAIL_CH.get(row, [ch])
                    railing  = any(cur_rails[c] for c in rail_chs if c < 8)
                    base_col = ROW_COLORS[row]
                    pen_col  = "#ef5350" if railing else base_col
                    cr = self._curves_raw[row]
                    cf = self._curves_filt[row]
                    cr_id = id(cr)
                    cf_id = id(cf)
                    if show_raw:
                        # raw mode: show unfiltered signal at full opacity, hide filtered
                        if _last_pen_col.get(cr_id) != pen_col:
                            cr.setPen(pg.mkPen(pen_col, width=1))
                            _last_pen_col[cr_id] = pen_col
                        cr.setData(t_ax, raw_disp[:, ch] / scale + y_off)
                        if not _last_vis.get(cr_id, False):
                            cr.setVisible(True)
                            _last_vis[cr_id] = True
                        if _last_vis.get(cf_id, True):
                            cf.setVisible(False)
                            _last_vis[cf_id] = False
                    else:
                        # default: filtered only, hide raw
                        if _last_vis.get(cr_id, False):
                            cr.setVisible(False)
                            _last_vis[cr_id] = False
                        if not _last_vis.get(cf_id, False):
                            cf.setVisible(True)
                            _last_vis[cf_id] = True
                        if _last_pen_col.get(cf_id) != pen_col:
                            cf.setPen(pg.mkPen(pen_col, width=1))
                            _last_pen_col[cf_id] = pen_col
                        cf.setData(t_ax, filt_disp[:, ch] / scale + y_off)
            _t4 = time.perf_counter()

            # 5. BIAS status (RMS of last 50 samples — large RMS = bias amp working hard)
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
                self._lbl_drl.setStyleSheet(
                    _LABEL_STYLE.format(fg=bc))

            # 6. Connection status
            if TEST_MODE:
                self._lbl_conn.setText("TEST")
            elif BLE_CLIENT.connected:
                self._lbl_conn.setText("LIVE")
                self._lbl_conn.setStyleSheet(
                    _LABEL_STYLE.format(fg="#4caf50"))
            else:
                self._lbl_conn.setText("Scanning…")
                self._lbl_conn.setStyleSheet(
                    _LABEL_STYLE.format(fg="#ffa726"))

            # 7. Heart rate (throttled)
            _hr_counter[0] += 1
            if _hr_counter[0] >= HR_EVERY:
                _hr_counter[0] = 0
                n_ecg = min(int(FS * 10), len(STATE.filt_buf))
                if n_ecg >= FS * 2:
                    with STATE.lock:
                        ecg_sig = STATE.filt_buf.last(n_ecg)[:, 1]
                    peaks, _ = find_peaks(
                        ecg_sig,
                        distance=int(FS * 0.30),
                        height=np.percentile(ecg_sig, 60),
                    )
                    if len(peaks) >= 2:
                        rr = np.diff(peaks) / FS
                        rr = rr[(rr > 0.3) & (rr < 2.0)]
                        if len(rr) >= 1:
                            bpm = 60.0 / np.mean(rr)
                            self._lbl_bpm.setText(f"{bpm:.0f}")

            # 8. FFT (throttled, only when live PSD on)
            if STATE.fft_live:
                _fft_counter[0] += 1
                if _fft_counter[0] >= FFT_EVERY:
                    _fft_counter[0] = 0
                    n_fft = min(4 * FS, len(STATE.filt_buf))
                    if n_fft >= 64:
                        with STATE.lock:
                            fft_data = STATE.filt_buf.last(n_fft)
                        eeg_rows = ["EEG occipital", "EEG central", "EEG frontal"]
                        all_psd = []
                        for row in eeg_rows:
                            ci = DERIVED_LABELS.index(row)
                            f, pxx = welch(fft_data[:, ci], fs=FS,
                                           nperseg=min(256, n_fft),
                                           noverlap=min(128, n_fft // 2))
                            mask = f <= 50
                            # pass log10 values — plot uses linear y axis
                            pxx_db = np.log10(np.maximum(pxx[mask], 1e-10))
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
                n = _pt["n"]
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
        self._timer.setInterval(16)   # ~60 fps target
        self._timer.timeout.connect(_update)
        self._timer.start()

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
        self._btn_raw     = _btn("raw: off",   fg="#666666")
        self._btn_notch   = _btn("60Hz: on",   fg="#66bb6a")
        self._btn_band    = _btn("Full",        fg="#ab47bc")
        self._btn_fft     = _btn("PSD: off",   fg="#666666")
        self._btn_ascale  = _btn("auto-scale", fg="#ffd54f")
        self._btn_hwtest  = _btn("test: off",  fg="#555555")
        self._btn_drl     = _btn("DRL: on",    fg="#66bb6a")
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
        pi.getAxis("left").setWidth(155)

        self._curves_filt = {}
        self._curves_raw  = {}
        self._curves_bias = None

        for row_i, row in enumerate(DISPLAY_ROWS):
            c = ROW_COLORS[row]
            y_off = float(N_ROWS - 1 - row_i)
            if row == "BIAS":
                cur = pg.PlotCurveItem(
                    pen=pg.mkPen(c, width=1, style=QtCore.Qt.DashLine))
                self._plot_sig.addItem(cur)
                self._curves_bias = cur
            else:
                # raw trace — shown at full opacity in raw mode, hidden by default
                cr = pg.PlotCurveItem(pen=pg.mkPen(c, width=1))
                cr.setVisible(False)
                self._plot_sig.addItem(cr)
                self._curves_raw[row] = cr
                # filtered
                cf = pg.PlotCurveItem(pen=pg.mkPen(c, width=1))
                self._plot_sig.addItem(cf)
                self._curves_filt[row] = cf

        self._refresh_yticks()

        # Test mode overlay — shown in the signal plot when HW test signal is active
        self._test_overlay = pg.TextItem(
            "TEST MODE  —  ~1 Hz internal square wave  ±1.875 mV",
            color=(255, 167, 38),   # amber
            anchor=(0.0, 1.0))
        self._test_overlay.setPos(0.05, N_ROWS - 0.1)
        self._test_overlay.setFont(QtGui.QFont("monospace", 9))
        self._plot_sig.addItem(self._test_overlay)
        self._test_overlay.setVisible(False)

        # Cursor crosshair — vertical dashed line + value label, shown on mouse hover
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
        # Y axis shows log10(PSD); we label manually as "PSD (µV²/Hz)"
        pf.setYRange(np.log10(0.01), np.log10(1e5), padding=0)
        pf.getAxis("bottom").setLabel("Frequency (Hz)", color="#666666")
        pf.getAxis("bottom").setTextPen(pg.mkPen("#666666"))
        pf.getAxis("left").setLabel("log PSD", color="#666666")
        pf.getAxis("left").setTextPen(pg.mkPen("#666666"))

        # Band region shading
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

        eeg_rows = ["EEG occipital", "EEG central", "EEG frontal"]
        self._curves_fft = {}
        for row in eeg_rows:
            cur = pg.PlotCurveItem(
                pen=pg.mkPen(ROW_COLORS[row], width=1), alpha=0.85)
            self._plot_fft.addItem(cur)
            self._curves_fft[row] = cur
        self._curve_fft_mean = pg.PlotCurveItem(
            pen=pg.mkPen("w", width=1.5, style=QtCore.Qt.DashLine))
        self._plot_fft.addItem(self._curve_fft_mean)
        self._plot_fft.setVisible(False)   # hidden until PSD toggled on

        root.addWidget(self._plot_fft, stretch=30)

        # ── Bottom control bar ────────────────────────────────────────────
        bot = QtWidgets.QHBoxLayout()
        bot.setSpacing(6)

        # Gain sliders
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
            val_lbl.setStyleSheet("color: #4fc3f7; font-size: 7pt; font-family: monospace;")
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
            "ECG HR", styleSheet="color:#555555;font-size:6pt;font-family:monospace;"))
        self._lbl_bpm = QtWidgets.QLabel("—")
        self._lbl_bpm.setStyleSheet(
            "color:#ef5350;font-size:14pt;font-family:monospace;font-weight:bold;")
        self._lbl_bpm.setAlignment(QtCore.Qt.AlignCenter)
        hr_vl.addWidget(self._lbl_bpm)
        hr_vl.addWidget(QtWidgets.QLabel(
            "bpm", styleSheet="color:#555555;font-size:6pt;font-family:monospace;"))
        bot.addWidget(hr_box)

        # Status box (DRL + conn)
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

        # Battery / PMIC status box
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
        self.resize(1400, 860)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _wire_callbacks(self):
        pi = self._plot_sig.plotItem

        # Window size
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

        # Raw overlay
        def toggle_raw():
            STATE.show_raw = not STATE.show_raw
            lbl = "raw: on" if STATE.show_raw else "raw: off"
            col = "#4fc3f7" if STATE.show_raw else "#666666"
            self._btn_raw.setText(lbl)
            self._btn_raw.setStyleSheet(_BTN_STYLE.format(bg="#1a1a1a", fg=col))
        self._btn_raw.clicked.connect(toggle_raw)
        self._toggle_raw = toggle_raw

        # Notch
        def toggle_notch():
            on = not self.proc.notch_on
            self.proc.set_notch(on)
            lbl = "60Hz: on" if on else "60Hz: off"
            col = "#66bb6a" if on else "#ef5350"
            self._btn_notch.setText(lbl)
            self._btn_notch.setStyleSheet(_BTN_STYLE.format(bg="#1a1a1a", fg=col))
        self._btn_notch.clicked.connect(toggle_notch)
        self._toggle_notch = toggle_notch

        # Band
        from eeg_processing import BAND_RANGES
        _band_list = list(BAND_RANGES.keys())
        def cycle_band():
            idx = _band_list.index(self.proc.band_mode)
            new = _band_list[(idx + 1) % len(_band_list)]
            self.proc.set_band(new)
            STATE.band_mode = new
            self._btn_band.setText(new)
        self._btn_band.clicked.connect(cycle_band)
        self._cycle_band = cycle_band

        # PSD
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

        # Gain sliders
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

        # Auto-scale — fit each row's ±scale to 95th percentile of current window
        def do_autoscale():
            with STATE.lock:
                n = min(STATE.window_samples, len(STATE.filt_buf))
                if n < 8:
                    return
                filt  = STATE.filt_buf.last(n)   # (N, 6)
                bias  = STATE.bias_buf.last(n)    # (N,)
            for row_i, row in enumerate(DISPLAY_ROWS):
                if row == "BIAS":
                    sig = bias
                else:
                    ci  = DERIVED_LABELS.index(row)
                    sig = filt[:, ci]
                p95 = float(np.percentile(np.abs(sig), 95))
                STATE.row_scales[row] = max(p95 * 1.2, 1.0)  # 20 % headroom
            self._refresh_yticks()
        self._btn_ascale.clicked.connect(do_autoscale)
        self._do_autoscale = do_autoscale

        # HW test mode — toggles ADS1299 internal calibration square wave
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

        # DRL toggle — enables/disables the Driven Right Leg noise-cancellation circuit
        #
        # Without DRL the body common-mode (60 Hz mains) can easily be ±1–10 V, which
        # saturates the ADC at gain 24 (full-scale = ±187 mV) and shows only flat rails.
        # To keep the 60 Hz noise *visible* for an SNR comparison, we drop all gains to
        # 1 when DRL is turned off (full-scale ±4.5 V — well within mains common-mode
        # range) and restore the user's previous gains when DRL is turned back on.
        _gains_before_drl_off = [None, None, None, None]   # saved gains per group

        def toggle_drl():
            STATE.drl_active = not STATE.drl_active
            on = STATE.drl_active
            self._btn_drl.setText("DRL: on" if on else "DRL: off")
            self._btn_drl.setStyleSheet(
                _BTN_STYLE.format(bg="#1a1a1a", fg="#66bb6a" if on else "#ef5350"))

            if not on:
                # DRL going off — save current gains, slam everything to ×1 so the
                # large common-mode doesn't rail the ADC and you can see the 60 Hz noise.
                for gi in range(4):
                    _gains_before_drl_off[gi] = STATE.gain[gi]
                    self._gain_sliders[gi].setValue(GAIN_VALUES.index(1))
                    # slider valueChanged fires make_gain_cb which updates STATE + firmware
            else:
                # DRL coming back on — restore gains if we saved them.
                if _gains_before_drl_off[0] is not None:
                    for gi in range(4):
                        self._gain_sliders[gi].setValue(
                            GAIN_VALUES.index(_gains_before_drl_off[gi]))
                    for gi in range(4):
                        _gains_before_drl_off[gi] = None

            self.proc.reset_state()   # clear filter transients from the gain change

            if not TEST_MODE:
                BLE_CLIENT.send_drl(on)
        self._btn_drl.clicked.connect(toggle_drl)
        self._toggle_drl = toggle_drl

        # REC / Save
        def toggle_rec():
            if not STATE.recording:
                STATE.recording = True
                STATE.rec_start = time.time()
                STATE.rec_buf   = []
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
            buf = list(STATE.rec_buf)
            if not buf:
                print("[SAVE] Nothing recorded yet."); return
            fname = save_edf(buf, STATE.gain[0], rec_start=STATE.rec_start)
            if fname:
                self._btn_save.setText("Saved")
                self._btn_save.setStyleSheet(
                    _BTN_STYLE.format(bg="#1a1a1a", fg="#333333"))
                STATE.rec_buf = []
        self._btn_save.clicked.connect(do_save)
        self._do_save = do_save

        # Cursor hover — value readout on mouse move over signal plot
        self._cursor_proxy = pg.SignalProxy(
            self._plot_sig.scene().sigMouseMoved,
            rateLimit=60,
            slot=lambda ev: self._on_mouse_moved(ev[0]))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _refresh_yticks(self):
        ticks = []
        for i, row in enumerate(DISPLAY_ROWS):
            y = float(N_ROWS - 1 - i)
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

        # Nearest row to cursor y position
        row_i = N_ROWS - 1 - int(round(y))
        row_i = max(0, min(N_ROWS - 1, row_i))
        row   = DISPLAY_ROWS[row_i]

        # Sample value at cursor x from the active curve for that row
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

        # Position label: flip to left side when cursor is in the right 25% of window
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
        k = ev.text()
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
        elif k in set("1234567"):
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
        BLE_CLIENT.stop()
        print("Exited.")
        ev.accept()


# ──────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="EEG Wearable Visualizer (pyqtgraph)")
    parser.add_argument("--test", action="store_true",
                        help="Run with synthetic data (no hardware needed)")
    args = parser.parse_args()

    global TEST_MODE
    if args.test:
        TEST_MODE = True

    print("=" * 58)
    print("  EEG Wearable Visualizer  [pyqtgraph]")
    print(f"  Mode   : {'TEST — synthetic data' if TEST_MODE else 'LIVE BLE'}")
    print(f"  FS     : {FS} Hz   |   Rows: {N_ROWS}")
    print("  Keys   : n b u f w r s a t  |  1-7 select row  |  +/- zoom  (7=BIAS, a=auto-scale, t=HW test)")
    print("  Scroll : zoom row under cursor  |  q: quit")
    print("=" * 58)

    proc     = EEGProcessor(fs=FS)
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
