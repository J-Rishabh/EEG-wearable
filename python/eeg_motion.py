"""
eeg_motion.py — Motion artifact detection for the EEG wearable
===============================================================
Single source of truth for all motion detection logic.  Used by:
  eeg_stream_pg.py              — live streaming  (stateful, per-sample)
  imu_motion_tuner.py           — live threshold tuning  (stateful, per-sample)
  experiments/ecg/hr_from_edf.py — offline post-processing  (batch)

Two interfaces
--------------
  ImuMotionDetector          Stateful class; call .process_sample() each IMU packet.
  imu_dynamic_accel_mask()   Offline batch version for EDF post-processing.
  detect_eeg_jump_mask()     Crude EEG-channel jump detector; OR with IMU mask to
                             catch electrode-pop artifacts the IMU cannot see.
"""

import math
import time
from typing import Optional

import numpy as np
from scipy.signal import lfilter

# ── Shared defaults ────────────────────────────────────────────────────────────
# These are tuned in imu_motion_tuner.py; copy any changes here and to eeg_stream_pg.py.
MOTION_THRESHOLD_MG = 30.0   # mg of smoothed dynamic accel to flag motion
MOTION_HOLDOFF_S    = 0.25   # seconds to keep flag raised after the last spike
IMU_GRAVITY_ALPHA   = 0.15   # gravity low-pass coefficient  (~0.7 Hz cut-off at 25 Hz)
IMU_DYNAMIC_ALPHA   = 0.20   # dynamic-accel smoothing coeff (~0.8 Hz cut-off at 25 Hz)


class ImuMotionDetector:
    """
    Stateful, per-sample IMU motion detector.

    Runs the same pipeline as imu_motion_tuner.py / eeg_stream_pg.py:
      Stage 1  gravity estimate:    recursive low-pass IIR on raw accel
                                    (cancels slow tilts / orientation changes)
      Stage 2  dynamic accel:       raw − gravity estimate (previous sample)
      Stage 3  smooth magnitude:    recursive low-pass IIR (suppresses noise spikes)
      Stage 4  threshold + holdoff: raises motion flag; holds for holdoff_s after last spike

    Tuning knobs (threshold_mg, holdoff_s, dynamic_alpha) are public attributes
    so imu_motion_tuner.py can update them live from the key handler.

    Usage
    -----
        detector = ImuMotionDetector()
        dyn_raw, dyn_smooth, is_motion = detector.process_sample(x_mg, y_mg, z_mg)
    """

    def __init__(
        self,
        threshold_mg:  float = MOTION_THRESHOLD_MG,
        holdoff_s:     float = MOTION_HOLDOFF_S,
        gravity_alpha: float = IMU_GRAVITY_ALPHA,
        dynamic_alpha: float = IMU_DYNAMIC_ALPHA,
    ):
        # Tuning knobs — safe to update between samples
        self.threshold_mg  = threshold_mg
        self.holdoff_s     = holdoff_s
        self.gravity_alpha = gravity_alpha
        self.dynamic_alpha = dynamic_alpha
        # Gravity estimate — public so imu_motion_tuner can read them for 3-D orientation
        self.gx: float = 0.0
        self.gy: float = 0.0
        self.gz: float = 1000.0
        # Internal filter / timing state
        self._dyn_smooth:    float = 0.0
        self._holdoff_until: float = 0.0

    def process_sample(
        self,
        x_mg: float,
        y_mg: float,
        z_mg: float,
        now:  Optional[float] = None,
    ) -> tuple:
        """
        Process one IMU sample.  Call from the BLE / IMU callback at 25 Hz.

        Parameters
        ----------
        x_mg, y_mg, z_mg : raw accelerometer readings in mg
        now               : wall-clock time (seconds); uses time.time() if None

        Returns
        -------
        dyn_raw    : instantaneous dynamic-accel magnitude in mg
        dyn_smooth : smoothed dynamic-accel magnitude in mg (what the threshold compares)
        motion     : True if motion is active (including holdoff period)
        """
        if now is None:
            now = time.time()
        # Stage 1: snapshot previous gravity, then update
        ga          = self.gravity_alpha
        gx, gy, gz  = self.gx, self.gy, self.gz
        self.gx = ga * x_mg + (1 - ga) * gx
        self.gy = ga * y_mg + (1 - ga) * gy
        self.gz = ga * z_mg + (1 - ga) * gz
        # Stage 2: dynamic accel = raw − previous gravity estimate
        dx, dy, dz = x_mg - gx, y_mg - gy, z_mg - gz
        dyn_raw    = math.sqrt(dx * dx + dy * dy + dz * dz)
        # Stage 3: smooth dynamic magnitude
        da               = self.dynamic_alpha
        self._dyn_smooth = da * dyn_raw + (1 - da) * self._dyn_smooth
        # Stage 4: threshold + holdoff
        if self._dyn_smooth > self.threshold_mg:
            self._holdoff_until = now + self.holdoff_s
        return dyn_raw, self._dyn_smooth, now < self._holdoff_until


# ── Offline / batch helpers ───────────────────────────────────────────────────

def imu_dynamic_accel_mask(
    accel_xyz:     np.ndarray,
    imu_fs:        float,
    eeg_n:         int,
    eeg_fs:        float,
    threshold_mg:  float = MOTION_THRESHOLD_MG,
    holdoff_s:     float = MOTION_HOLDOFF_S,
    gravity_alpha: float = IMU_GRAVITY_ALPHA,
    dynamic_alpha: float = IMU_DYNAMIC_ALPHA,
) -> tuple:
    """
    Offline batch equivalent of ImuMotionDetector for EDF post-processing.

    Uses scipy lfilter to run the same recursive IIR pipeline over the full
    recorded ACCEL_X / Y / Z channels, then upsamples the result to EEG rate.

    Parameters
    ----------
    accel_xyz : (N_imu, 3) raw accelerometer readings in mg
    imu_fs    : IMU sample rate (typically 25 Hz)
    eeg_n     : number of EEG samples (sets output mask length)
    eeg_fs    : EEG sample rate (typically 250 Hz)

    Returns
    -------
    motion_mask_eeg : boolean (eeg_n,)  — motion mask upsampled to EEG rate
    dyn_smooth      : float   (N_imu,)  — smoothed dynamic-accel trace in mg
    t_imu           : float   (N_imu,)  — time axis for the IMU data (seconds)
    """
    N_imu = len(accel_xyz)
    t_imu = np.arange(N_imu) / imu_fs

    # Stage 1: gravity estimate — IIR low-pass, initialised to first sample (no transient)
    b_g = [gravity_alpha]
    a_g = [1.0, -(1.0 - gravity_alpha)]
    gx = lfilter(b_g, a_g, accel_xyz[:, 0], zi=[(1 - gravity_alpha) * accel_xyz[0, 0]])[0]
    gy = lfilter(b_g, a_g, accel_xyz[:, 1], zi=[(1 - gravity_alpha) * accel_xyz[0, 1]])[0]
    gz = lfilter(b_g, a_g, accel_xyz[:, 2], zi=[(1 - gravity_alpha) * accel_xyz[0, 2]])[0]

    # Stage 2: dynamic accel
    dx = accel_xyz[:, 0] - gx
    dy = accel_xyz[:, 1] - gy
    dz = accel_xyz[:, 2] - gz
    dyn_raw = np.sqrt(dx**2 + dy**2 + dz**2)

    # Stage 3: smooth (zi=default 0, matching the live detector which starts at 0)
    b_d = [dynamic_alpha]
    a_d = [1.0, -(1.0 - dynamic_alpha)]
    dyn_smooth = lfilter(b_d, a_d, dyn_raw)

    # Stage 4: threshold + holdoff → boolean at IMU rate
    spikes        = dyn_smooth > threshold_mg
    holdoff_samps = int(np.ceil(holdoff_s * imu_fs))
    motion_imu    = spikes.copy()
    for idx in np.where(spikes)[0]:
        motion_imu[idx : min(N_imu, idx + holdoff_samps + 1)] = True

    # Stage 5: nearest-neighbour upsample to EEG rate
    t_eeg   = np.arange(eeg_n) / eeg_fs
    imu_idx = np.searchsorted(t_imu, np.clip(t_eeg, 0.0, t_imu[-1]), side="right") - 1
    imu_idx = np.clip(imu_idx, 0, N_imu - 1)

    return motion_imu[imu_idx], dyn_smooth, t_imu


def detect_eeg_jump_mask(
    sigs:    np.ndarray,
    fs:      float,
    jump_uv: float = 500.0,
    pad_ms:  float = 200.0,
) -> np.ndarray:
    """
    Crude EEG-channel artifact detector based on per-sample delta magnitude.

    Flags samples where *any* channel's sample-to-sample jump exceeds jump_uv.
    These sharp transients come from electrode pop / lead movement and are often
    not visible in the IMU signal (head is still, electrode shifts).

    Use as a supplement to imu_dynamic_accel_mask() — OR the two masks together.

    Parameters
    ----------
    sigs    : (N, n_ch) array — all EEG channel signals at fs Hz
    fs      : EEG sample rate in Hz
    jump_uv : per-sample delta threshold in µV  (default 500 µV/sample)
    pad_ms  : padding around each detected spike in ms  (default 200 ms)

    Returns
    -------
    mask : boolean (N,) — True where an EEG-jump artifact is present
    """
    diffs   = np.abs(np.diff(sigs, axis=0))        # (N-1, n_ch)
    metric  = diffs.max(axis=1)                     # (N-1,) — worst channel at each step
    mask    = np.concatenate([[False], metric > jump_uv])
    pad     = int(pad_ms * fs / 1000)
    dilated = mask.copy()
    for i in np.where(mask)[0]:
        dilated[max(0, i - pad) : min(len(mask), i + pad)] = True
    return dilated
