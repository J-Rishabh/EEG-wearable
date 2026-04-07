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
  u           toggle raw ghost overlay
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
import asyncio
import threading
import time
import struct
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

# ── pyqtgraph global config ────────────────────────────────────────────────────
# antialias=False: skip sub-pixel AA on lines (not needed for dense EEG traces)
# useOpenGL=True:  GPU line rendering — this is the whole point
pg.setConfigOptions(antialias=False, useOpenGL=True)

# ──────────────────────────────────────────────────────────────────────────────
# TOP-LEVEL CONFIGURATION
# ──────────────────────────────────────────────────────────────────────────────

TEST_MODE   = False
DEVICE_NAME = "EEG Wearable"
FS          = 250

EEG_CHAR_UUID  = "12340002-1234-1234-1234-123456789abc"
CTRL_CHAR_UUID = "12340004-1234-1234-1234-123456789abc"

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
        self.connected    = False
        self.ble_client   = None
        self.ble_loop     = None
        self.hw_test_mode = False   # True = ADS1299 internal square-wave test

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
# BLE FUNCTIONS  (identical to eeg_stream.py)
# ──────────────────────────────────────────────────────────────────────────────

_RAIL_THRESHOLD = int(0.90 * 8_388_607)

# ch_to_group[ch] = gain group index for channel ch (0-indexed).
# -1 = use gain=1 (CH7 powered down, CH8 BIAS fixed).
_CH_TO_GROUP = [0, 1, 1, 2, 3, 2, -1, -1]

def _parse_198(data: bytes):
    if len(data) < 198: return None
    idx   = struct.unpack_from("<H", data, 0)[0]
    gains = [data[2], data[3], data[4], data[5]]   # one per group
    raw8  = np.frombuffer(data[6:], dtype=np.uint8).reshape(8, 8, 3)
    vals  = np.zeros((8, 8), dtype=np.int32)
    for s in range(8):
        for ch in range(8):
            b = raw8[s, ch]
            v = (int(b[0]) << 16) | (int(b[1]) << 8) | int(b[2])
            if v & 0x800000: v -= 0x1000000
            vals[s, ch] = v
    # Per-channel gain: look up group, default to 1 for CH7/CH8
    ch_gains = np.array([
        gains[_CH_TO_GROUP[ch]] if _CH_TO_GROUP[ch] >= 0 else 1
        for ch in range(8)
    ], dtype=np.float64)
    uv = vals.astype(np.float64) * (4_500_000.0 / ch_gains / 8_388_608.0)
    rails = np.any(np.abs(vals) > _RAIL_THRESHOLD, axis=0)
    return idx, gains, uv, rails


_last_idx          = [-1]
_pkt_count         = [0]
_dbg_enabled       = [True]   # set False once data is confirmed flowing
_first_pkt_flag    = [False]  # set True by first _ble_notify call; reset on reconnect
_ble_stop          = [False]  # set True by closeEvent to signal BLE thread to exit

def _ble_notify(sender, data: bytes):
    _first_pkt_flag[0] = True
    _pkt_count[0] += 1
    n = _pkt_count[0]
    # Always print first packet; then every 250 (~8 s at 31 pkt/s) as heartbeat
    if n == 1 or n % 250 == 0:
        print(f"[DBG] notify #{n}  len={len(data)}")
    parsed = _parse_198(data)
    if parsed is None:
        print(f"[DBG] _parse_198 returned None — len={len(data)}")
        return
    idx, gains, uv, rails = parsed
    if n == 1:
        print(f"[DBG] parsed ok  idx={idx}  gains={gains}  "
              f"uv[0,0..3]={uv[0,:4].round(1)}")
    # Gap detection — firmware index increments by 8 per packet (8 samples/batch)
    if _last_idx[0] >= 0:
        expected = (_last_idx[0] + 8) & 0xFFFF
        if idx != expected:
            dropped = (idx - _last_idx[0] - 8) & 0xFFFF
            print(f"[BLE] Gap: expected idx={expected}, got {idx} "
                  f"(~{dropped} samples dropped)")
    _last_idx[0] = idx
    rows = [uv[s] for s in range(8)]
    with STATE.lock:
        STATE.pending.extend(rows)
        STATE.rails |= rails
        if STATE.recording: STATE.rec_buf.extend(rows)


async def _ble_run():
    from bleak import BleakScanner, BleakClient
    # Store loop so send_gain() can post coroutines from the Qt thread
    STATE.ble_loop = asyncio.get_event_loop()
    print(f"[BLE] Scanning for '{DEVICE_NAME}' …")
    dev = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=20.0)
    if dev is None:
        print("[BLE] Not found."); return
    print(f"[BLE] Connecting to {dev.address} …")
    for conn_attempt in range(20):
        if _ble_stop[0]:
            break
        # Reset data-arrival flag for this connection attempt
        _first_pkt_flag[0] = False

        try:
            async with BleakClient(dev, use_cached_services=False) as client:
                STATE.connected  = True
                STATE.ble_client = client
                if conn_attempt == 0:
                    print("[DBG] Services / characteristics:")
                    for svc in client.services:
                        print(f"  SVC {svc.uuid}")
                        for ch in svc.characteristics:
                            print(f"    CHAR {ch.uuid}  props={ch.properties}")

                # Wait 1.0 s for LL procedures (DLE, conn param update) to finish.
                # 0x23 LL_PROC_COLLISION typically occurs within ~0.8 s of connect.
                await asyncio.sleep(1.0)
                if not client.is_connected:
                    print(f"[BLE] conn {conn_attempt + 1}: dropped during settle — retrying")
                    STATE.connected  = False
                    STATE.ble_client = None
                    continue

                # Windows WinRT cancels CCCD write if MTU exchange is still in
                # progress (WinError -2147023673). Retry a few times within the
                # same connection — reconnecting each attempt makes things worse.
                subscribed = False
                for sub_attempt in range(4):
                    try:
                        if sub_attempt > 0:
                            await asyncio.sleep(0.5)
                        if not client.is_connected:
                            break
                        await client.start_notify(EEG_CHAR_UUID, _ble_notify)
                        subscribed = True
                        break
                    except Exception as e:
                        print(f"[BLE] start_notify {sub_attempt + 1}/4 failed: {e}")

                if not subscribed:
                    print(f"[BLE] conn {conn_attempt + 1}: could not subscribe — reconnecting")
                    STATE.connected  = False
                    STATE.ble_client = None
                    continue

                # Verify data actually arrives — Windows sometimes silently drops
                # the CCCD write so start_notify returns success but firmware never
                # enables notifications.
                print("[BLE] Subscribed — waiting for first packet …")
                for _ in range(100):   # 10 s timeout (100 × 0.1 s) — DLE takes ~3 s after CCCD, firmware streams ~2 s later
                    await asyncio.sleep(0.1)
                    if _first_pkt_flag[0]:
                        break
                    if not client.is_connected:
                        break
                else:
                    print(f"[BLE] conn {conn_attempt + 1}: no data in 4 s "
                          f"(CCCD not delivered?) — reconnecting")
                    STATE.connected  = False
                    STATE.ble_client = None
                    continue

                if not _first_pkt_flag[0]:
                    # Connection dropped before data arrived
                    print(f"[BLE] conn {conn_attempt + 1}: dropped before data — retrying")
                    STATE.connected  = False
                    STATE.ble_client = None
                    continue

                print("[BLE] Streaming.")
                while STATE.connected and client.is_connected:
                    await asyncio.sleep(0.1)

                try:
                    await client.stop_notify(EEG_CHAR_UUID)
                except Exception:
                    pass

                if not STATE.connected:
                    break   # window closed — stop
                # Otherwise: unexpected drop — fall through to reconnect
                print("[BLE] Connection dropped — reconnecting")
                STATE.connected  = False
                STATE.ble_client = None

        except Exception as e:
            STATE.connected  = False
            STATE.ble_client = None
            print(f"[BLE] conn {conn_attempt + 1}/20 exception: {e}")
        await asyncio.sleep(0.5)

    STATE.connected  = False
    STATE.ble_client = None


def ble_thread():
    asyncio.run(_ble_run())


async def _write_gain(group: int, g: int):
    if STATE.ble_client and STATE.ble_client.is_connected:
        await STATE.ble_client.write_gatt_char(
            CTRL_CHAR_UUID, bytes([group, g]), response=False)


async def _write_test_mode(enable: bool):
    if STATE.ble_client and STATE.ble_client.is_connected:
        # Byte 0 = 0xFF → test mode command; byte 1 = 1 enable / 0 disable
        await STATE.ble_client.write_gatt_char(
            CTRL_CHAR_UUID, bytes([0xFF, 0x01 if enable else 0x00]), response=False)


def send_gain(group: int, g: int):
    if STATE.ble_loop:
        asyncio.run_coroutine_threadsafe(_write_gain(group, g), STATE.ble_loop)


def send_test_mode(enable: bool):
    if STATE.ble_loop:
        asyncio.run_coroutine_threadsafe(_write_test_mode(enable), STATE.ble_loop)


# ──────────────────────────────────────────────────────────────────────────────
# EDF SAVE  (identical to eeg_stream.py)
# ──────────────────────────────────────────────────────────────────────────────

def save_edf(buf: list, gain: int):
    rec_dir = os.path.join(os.path.dirname(__file__), "recordings")
    os.makedirs(rec_dir, exist_ok=True)
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    arr = np.array(buf, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 8:
        print("[SAVE] Unexpected buffer shape — aborting."); return None
    try:
        import pyedflib
        fname = os.path.join(rec_dir, f"eeg_{ts}.edf")
        labels = ["EOG","EMG_far","EMG_near","EEG_L1","EEG_L2","EEG_L3","SRB1","DRL"]
        f = pyedflib.EdfWriter(fname, 8, file_type=pyedflib.FILETYPE_EDFPLUS)
        try:
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
    print(f"[SAVE] {arr.shape[0]} samples ({arr.shape[0]/FS:.1f} s) -> {fname}")
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

        def _update():
            _t0 = time.perf_counter()
            if _last_t[0]:
                _pt["interval"] += _t0 - _last_t[0]
            _last_t[0] = _t0

            # 1. Drain
            with STATE.lock:
                if not STATE.pending:
                    return
                batch = list(STATE.pending)
                STATE.pending.clear()
            _t1 = time.perf_counter()

            if _pt["n"] < 3:
                print(f"[DBG] _update draining {len(batch)} samples")

            batch_np = np.array(batch, dtype=np.float64)
            if _pt["n"] < 3:
                print(f"[DBG] batch_np shape={batch_np.shape}")

            # 2. Derive + filter (skip filter in HW test mode — keep square edges sharp)
            derived_raw  = derive_signals(batch_np)
            derived_filt = derived_raw if STATE.hw_test_mode else proc.process(derived_raw)
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

            step = max(1, n_disp // 1200)
            t_ax      = np.linspace(0, ws / FS, n_disp)[::step]
            filt_disp = filt_disp[::step]
            raw_disp  = raw_disp[::step]
            bias_disp = bias_disp[::step]

            # 4. Update signal curves + railing flash
            # cur_rails[0..5] maps to CH1..CH6; derived rows map CH→rail index
            # EOG=CH1(0), ECG=CH2(1), EMG=CH2-CH3(1 or 2), EEG rows=CH4-6(3-5)
            _row_rail_ch = {
                "EOG": [0], "ECG": [1], "EMG": [1, 2],
                "EEG occipital": [3, 5], "EEG central": [4], "EEG frontal": [5],
            }
            for row_i, row in enumerate(DISPLAY_ROWS):
                y_off  = float(N_ROWS - 1 - row_i)
                scale  = max(STATE.row_scales.get(row, 100.0), 1.0)
                if row == "BIAS":
                    self._curves_bias.setData(t_ax, bias_disp / scale + y_off)
                else:
                    ch = DERIVED_LABELS.index(row)
                    # railing: flash trace red if any contributing channel rails
                    rail_chs = _row_rail_ch.get(row, [ch])
                    railing  = any(cur_rails[c] for c in rail_chs if c < 8)
                    base_col = ROW_COLORS[row]
                    pen_col  = "#ef5350" if railing else base_col
                    if STATE.show_raw:
                        # raw-only: show unfiltered at full opacity, hide filtered
                        self._curves_filt[row].setVisible(False)
                        self._curves_raw[row].setPen(pg.mkPen(pen_col, width=1))
                        self._curves_raw[row].setData(
                            t_ax, raw_disp[:, ch] / scale + y_off)
                        self._curves_raw[row].setVisible(True)
                    else:
                        # normal: show filtered, hide raw
                        self._curves_raw[row].setVisible(False)
                        self._curves_filt[row].setVisible(True)
                        self._curves_filt[row].setPen(pg.mkPen(pen_col, width=1))
                        self._curves_filt[row].setData(
                            t_ax, filt_disp[:, ch] / scale + y_off)
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
            elif STATE.connected:
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
        for b in (self._btn_raw, self._btn_notch, self._btn_band,
                  self._btn_fft, self._btn_ascale, self._btn_hwtest):
            b.setFixedWidth(80)
            top.addWidget(b)
        top.addStretch()

        root.addLayout(top)

        # ── Signal plot ───────────────────────────────────────────────────
        self._plot_sig = _SigPlotWidget(
            self,
            background="#111111",
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
                # raw trace — full opacity, hidden until raw mode is toggled on
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
                if not TEST_MODE: send_gain(idx, g)
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
                send_test_mode(on)
        self._btn_hwtest.clicked.connect(toggle_hwtest)
        self._toggle_hwtest = toggle_hwtest

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
            fname = save_edf(buf, STATE.gain[0])
            if fname:
                self._btn_save.setText("Saved")
                self._btn_save.setStyleSheet(
                    _BTN_STYLE.format(bg="#1a1a1a", fg="#333333"))
                STATE.rec_buf = []
        self._btn_save.clicked.connect(do_save)
        self._do_save = do_save

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _refresh_yticks(self):
        ticks = []
        for i, row in enumerate(DISPLAY_ROWS):
            y = float(N_ROWS - 1 - i)
            sc = STATE.row_scales.get(row, 100.0)
            sc_str = f"{sc:.0f}" if sc >= 10 else f"{sc:.1f}"
            ticks.append((y, f"{row}  ±{sc_str}µV"))
        self._plot_sig.plotItem.getAxis("left").setTicks([ticks])

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
        STATE.connected = False
        _ble_stop[0] = True
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
        t = threading.Thread(target=ble_thread, daemon=True)
        t.start()
        print(f"[BLE] Scanning for '{DEVICE_NAME}' …")

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = EEGWindow(proc)
    win.show()

    try:
        sys.exit(app.exec_())
    finally:
        stop_evt.set()
        STATE.connected = False


if __name__ == "__main__":
    main()
