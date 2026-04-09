"""
eog_processing.py — EOG / blink signal processing
===================================================
Classes
-------
  EOGSaccadeDetector  — left/right saccade → steering [-1, 1]
  EOGPassthrough      — raw HP-filtered amplitude → steering [-1, 1]
  BlinkDetector       — blink detection on any channel → pop_blink()

Pipeline (all classes)
----------------------
  raw µV  →  HP filter (0.5–1 Hz)  →  60 Hz notch  →  threshold detector

Tuning
------
  threshold_uv  : size of deflection that counts as a saccade / blink.
                  EOG saccade: 10–200 µV.  Blink: 100–400 µV on EOG,
                  50–150 µV on EEG central.  Start high and lower slowly.
  debounce_s    : minimum gap between events.  0.4–0.5 s feels natural.
"""

import collections
import threading
import numpy as np
from scipy.signal import butter, iirnotch, sosfilt, tf2sos

FS_DEFAULT = 250   # ADS1299 default sample rate


# ── EOGSaccadeDetector ────────────────────────────────────────────────────────

class EOGSaccadeDetector:
    """
    Stateful saccade detector.  Feed it CH1 samples as they arrive and
    call process() — it returns the current steering value.
    """

    def __init__(self,
                 fs: int   = FS_DEFAULT,
                 threshold_uv: float = 50.0,
                 debounce_s:   float = 0.4,
                 hold_s:       float = 0.3,
                 notch_hz:     float = 60.0,
                 notch_on:     bool  = True):

        self.fs           = fs
        self.threshold_uv = threshold_uv
        self.notch_on     = notch_on

        self._debounce    = int(debounce_s * fs)
        self._hold        = int(hold_s * fs)
        self._cooldown    = 0
        self._hold_left   = 0
        self._steering    = 0.0

        # High-pass filter (0.5 Hz, 2nd-order Butterworth)
        sos_hp = butter(2, 0.5 / (fs / 2), btype='high', output='sos')
        self._sos_hp = sos_hp
        self._zi_hp  = np.zeros((sos_hp.shape[0], 2), dtype=np.float64)

        # Notch filter (60 Hz)
        b, a = iirnotch(notch_hz, Q=30, fs=fs)
        self._sos_notch = tf2sos(b, a)
        self._zi_notch  = np.zeros((self._sos_notch.shape[0], 2), dtype=np.float64)

    def reset(self):
        """Reset filter states and steering output (call after a long gap)."""
        self._zi_hp     = np.zeros_like(self._zi_hp)
        self._zi_notch  = np.zeros_like(self._zi_notch)
        self._cooldown  = 0
        self._hold_left = 0
        self._steering  = 0.0

    def process(self, eog_samples: np.ndarray) -> float:
        """
        Feed new EOG samples (µV) from CH1. Shape: (N,).
        Returns current steering value in [-1.0, 1.0].
        """
        if len(eog_samples) == 0:
            return self._steering

        sig = eog_samples.astype(np.float64)
        sig, self._zi_hp = sosfilt(self._sos_hp, sig, zi=self._zi_hp)
        if self.notch_on:
            sig, self._zi_notch = sosfilt(self._sos_notch, sig, zi=self._zi_notch)

        for sample in sig:
            if self._hold_left > 0:
                self._hold_left -= 1
                if self._hold_left == 0:
                    self._steering = 0.0
            if self._cooldown > 0:
                self._cooldown -= 1
                continue
            if sample > self.threshold_uv:
                self._steering  =  1.0
                self._cooldown  = self._debounce
                self._hold_left = self._hold
            elif sample < -self.threshold_uv:
                self._steering  = -1.0
                self._cooldown  = self._debounce
                self._hold_left = self._hold

        return self._steering

    @property
    def steering(self) -> float:
        return self._steering


# ── EOGPassthrough ────────────────────────────────────────────────────────────

class EOGPassthrough:
    """
    Passes the HP-filtered EOG amplitude directly as a continuous steering
    signal — useful for debugging electrode placement.
    """

    def __init__(self, fs: int = FS_DEFAULT, scale_uv: float = 100.0):
        self.scale_uv  = scale_uv
        sos_hp         = butter(2, 0.5 / (fs / 2), btype='high', output='sos')
        self._sos_hp   = sos_hp
        self._zi_hp    = np.zeros((sos_hp.shape[0], 2), dtype=np.float64)
        self._steering = 0.0

    def process(self, eog_samples: np.ndarray) -> float:
        if len(eog_samples) == 0:
            return self._steering
        sig, self._zi_hp = sosfilt(self._sos_hp, eog_samples.astype(np.float64),
                                   zi=self._zi_hp)
        self._steering = float(np.clip(sig[-1] / self.scale_uv, -1.0, 1.0))
        return self._steering

    @property
    def steering(self) -> float:
        return self._steering


# ── BlinkDetector ─────────────────────────────────────────────────────────────

class BlinkDetector:
    """
    Detects eye blinks as large-amplitude transients on any EEG/EOG channel.

    Thread-safe: call process() from the BLE thread, pop_blink() / last_amplitude
    from the game / main thread.

    Properties
    ----------
    monitor_signal   : np.ndarray  — rolling buffer of HP-filtered samples (µV)
    last_amplitude   : float       — most recent filtered sample value (µV)
    """

    def __init__(self,
                 fs:           int   = FS_DEFAULT,
                 threshold_uv: float = 150.0,
                 debounce_s:   float = 0.5,
                 monitor_secs: float = 3.0):

        self.fs           = fs
        self.threshold_uv = threshold_uv
        self._debounce    = int(debounce_s * fs)
        self._cooldown    = 0
        self._blinked     = False
        self._last_amp    = 0.0   # most recent filtered sample — GIL-safe float
        self._lock        = threading.Lock()

        self._monitor: collections.deque = collections.deque(
            maxlen=int(fs * monitor_secs))

        # 1 Hz HP — removes DC / slow drift
        sos_hp = butter(2, 1.0 / (fs / 2), btype='high', output='sos')
        self._sos_hp   = sos_hp
        self._zi_hp    = np.zeros((sos_hp.shape[0], 2), dtype=np.float64)

        # 60 Hz notch
        b, a = iirnotch(60.0, Q=30, fs=fs)
        self._sos_notch = tf2sos(b, a)
        self._zi_notch  = np.zeros((self._sos_notch.shape[0], 2), dtype=np.float64)

    def process(self, samples: np.ndarray):
        """Feed new samples (µV, 1-D). Call from the BLE thread."""
        if len(samples) == 0:
            return
        sig = samples.astype(np.float64)
        sig, self._zi_hp    = sosfilt(self._sos_hp,    sig, zi=self._zi_hp)
        sig, self._zi_notch = sosfilt(self._sos_notch, sig, zi=self._zi_notch)

        self._monitor.extend(sig.tolist())
        self._last_amp = float(sig[-1])

        for s in sig:
            if self._cooldown > 0:
                self._cooldown -= 1
                continue
            if abs(s) > self.threshold_uv:
                with self._lock:
                    self._blinked = True
                self._cooldown = self._debounce

    def pop_blink(self) -> bool:
        """Returns True once after each blink. Thread-safe."""
        with self._lock:
            if self._blinked:
                self._blinked = False
                return True
        return False

    @property
    def last_amplitude(self) -> float:
        """Most recent HP-filtered sample in µV. GIL-safe."""
        return self._last_amp

    @property
    def monitor_signal(self) -> np.ndarray:
        """Rolling buffer of HP-filtered samples for waveform display."""
        return np.array(self._monitor, dtype=np.float64)
