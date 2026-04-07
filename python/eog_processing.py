"""
eog_processing.py — EOG saccade detection for eye-controlled game
==================================================================
Takes raw µV samples from ADS1299 CH1 (EOG electrode) and outputs
a steering signal in [-1.0, 1.0] by detecting left/right eye saccades.

Pipeline
--------
  CH1 raw (µV)
      │
      ▼
  0.5 Hz 2nd-order Butterworth high-pass   ← removes slow electrode drift / DC
      │
      ▼
  60 Hz notch (Q=30)                       ← removes mains interference
      │
      ▼
  Threshold crossing detector              ← saccade = fast transient above threshold
      │                                       right dart  →  +1.0
      ▼                                       left  dart  →  -1.0
  Debounce + hold                          ← prevents rapid re-triggering
      │
      ▼
  Steering signal [-1.0, 1.0]

Tuning
------
threshold_uv   — how large a deflection counts as a saccade.
                 Typical EOG saccade: 10–200 µV depending on electrode placement
                 and angle of gaze shift.  Start at 50 µV and adjust.

debounce_s     — minimum time between saccades.  0.4 s feels natural for gaming.

hold_s         — how long the steering signal stays at ±1 after detection.
                 0.3 s gives a responsive feel without drifting.

Usage
-----
    detector = EOGSaccadeDetector(fs=250)

    # Feed new samples from BLE callback:
    steering = detector.process(samples[:, 0])   # CH1 column = EOG

    # Threshold can be updated live:
    detector.threshold_uv = 80.0
"""

import numpy as np
from scipy.signal import butter, iirnotch, sosfilt, sosfilt_zi, tf2sos

FS_DEFAULT = 250   # ADS1299 default sample rate


class EOGSaccadeDetector:
    """
    Stateful saccade detector.  Feed it CH1 samples as they arrive and
    call `process()` — it returns the current steering value.
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
        self._cooldown    = 0   # samples remaining before next detection allowed
        self._hold_left   = 0   # samples remaining to hold steering output
        self._steering    = 0.0

        # ---- High-pass filter (0.5 Hz, 2nd-order Butterworth) ----
        sos_hp = butter(2, 0.5 / (fs / 2), btype='high', output='sos')
        self._sos_hp = sos_hp
        self._zi_hp  = np.zeros((sos_hp.shape[0], 2), dtype=np.float64)

        # ---- Notch filter (60 Hz) ----
        b_notch, a_notch = iirnotch(notch_hz, Q=30, fs=fs)
        self._sos_notch = tf2sos(b_notch, a_notch)
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
        Call this every time new BLE data arrives.
        """
        if len(eog_samples) == 0:
            return self._steering

        sig = eog_samples.astype(np.float64)

        # High-pass
        sig, self._zi_hp = sosfilt(self._sos_hp, sig, zi=self._zi_hp)

        # Notch
        if self.notch_on:
            sig, self._zi_notch = sosfilt(self._sos_notch, sig, zi=self._zi_notch)

        # Saccade detection — scan sample by sample
        for sample in sig:
            # Decay hold timer
            if self._hold_left > 0:
                self._hold_left -= 1
                if self._hold_left == 0:
                    self._steering = 0.0

            # Respect debounce
            if self._cooldown > 0:
                self._cooldown -= 1
                continue

            # Threshold crossing → saccade detected
            if sample > self.threshold_uv:
                self._steering  =  1.0    # right
                self._cooldown  = self._debounce
                self._hold_left = self._hold
            elif sample < -self.threshold_uv:
                self._steering  = -1.0    # left
                self._cooldown  = self._debounce
                self._hold_left = self._hold

        return self._steering

    @property
    def steering(self) -> float:
        """Current steering value without feeding new samples."""
        return self._steering


class EOGPassthrough:
    """
    Drop-in replacement for EOGSaccadeDetector that passes the raw
    (HP-filtered) EOG amplitude directly as a continuous steering signal.
    Useful for debugging / calibrating electrode placement — you can see
    the raw signal reflected in the car's steering rather than discrete
    saccade events.
    """

    def __init__(self, fs: int = FS_DEFAULT, scale_uv: float = 100.0):
        self.scale_uv = scale_uv   # µV that maps to full ±1.0 steering
        sos_hp = butter(2, 0.5 / (fs / 2), btype='high', output='sos')
        self._sos_hp = sos_hp
        self._zi_hp  = np.zeros((sos_hp.shape[0], 2), dtype=np.float64)
        self._steering = 0.0

    def process(self, eog_samples: np.ndarray) -> float:
        if len(eog_samples) == 0:
            return self._steering
        sig, self._zi_hp = sosfilt(self._sos_hp, eog_samples.astype(np.float64),
                                   zi=self._zi_hp)
        # Use last sample, clamp to ±1
        self._steering = float(np.clip(sig[-1] / self.scale_uv, -1.0, 1.0))
        return self._steering

    @property
    def steering(self) -> float:
        return self._steering
