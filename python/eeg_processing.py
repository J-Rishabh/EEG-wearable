"""
eeg_processing.py — Signal processing module for the EEG wearable visualizer
==============================================================================
Handles:
  - Channel definitions and derived-signal derivation (CH1–CH8 → 6 display rows)
  - EEGProcessor: stateful IIR filter chain (60 Hz notch, 0.5 Hz HP, 40 Hz LP/band)
  - Band modes: Full, Delta, Theta, Alpha, Beta, Gamma
  - All filter objects are built once at __init__; zi is maintained per-call

Widmann et al. 2015: LP Butterworth 4th-order at 40 Hz is used for Full mode.
For band modes the LP SOS stage is replaced by a bandpass; the HP stage is
always applied so that DC drift is always removed.

Dependencies: numpy, scipy
"""

import numpy as np
from scipy.signal import butter, iirnotch, sosfilt, sosfilt_zi, tf2sos

# ---------------------------------------------------------------------------
# Channel / signal constants
# ---------------------------------------------------------------------------

# Physical ADS1299 channel labels (0-indexed, matches raw_uv columns)
PHYSICAL_CH = [
    "CH1_EOG",      # 0
    "CH2_EMG_far",  # 1
    "CH3_EMG_near", # 2
    "CH4_EEG_L1",   # 3
    "CH5_EEG_L2",   # 4
    "CH6_EEG_L3",   # 5
    "CH7_PWDN",     # 6  — powered down in firmware (IN7P/IN7N tied to +5V)
    "CH8_BIAS",     # 7  — BIASOUT_DRN via MUX=110 (measures bias drive quality)
]

# Derived display row labels (6 rows shown in the stacked signal viewer)
DERIVED_LABELS = [
    "EOG",          # 0  = CH1  (EOG − SRB1 reference, referential)
    "ECG",          # 1  = CH2  (EMG_far − SRB1 reference, referential)
    "EMG",          # 2  = CH2 − CH3  (EMG_far minus EMG_near, bipolar)
    "EEG occipital",  # 3  = CH4 − CH6  (EEG_L1 minus EEG_L3, bipolar)
    "EEG central",  # 4  = CH5  (EEG_L2 − SRB1 reference, referential)
    "EEG frontal",  # 5  = CH6  (EEG_L3 − SRB1 reference, referential)
    # CH7 (powered down) and CH8 (BIAS) handled separately outside filter chain
]

# Default ±µV display scale for each derived row
DEFAULT_SCALE_UV = {
    "EOG":          500.0,
    "ECG":         1500.0,
    "EMG":          200.0,
    "EEG occipital":  100.0,
    "EEG central":  100.0,
    "EEG frontal":  100.0,
    # Extra entry used by the viewer for the BIAS (CH8 BIASOUT_DRN) row
    "BIAS":          10.0,
}

NUM_DERIVED = len(DERIVED_LABELS)   # 6 — number of filter-chain rows

# Band-pass frequency ranges in Hz (used when band_mode != "Full")
BAND_RANGES = {
    "Full":   None,          # use LP only (0.5–40 Hz after HP)
    "Delta":  (0.5,  4.0),
    "Theta":  (4.0,  8.0),
    "Alpha":  (8.0, 13.0),
    "Beta":  (13.0, 30.0),
    "Gamma": (30.0, 40.0),
}

BAND_COLORS = {
    "Delta":  "#4C72B0",
    "Theta":  "#DD8452",
    "Alpha":  "#55A868",
    "Beta":   "#C44E52",
    "Gamma":  "#8172B2",
}

# ---------------------------------------------------------------------------
# Derivation helper
# ---------------------------------------------------------------------------

def derive_signals(raw_uv: np.ndarray) -> np.ndarray:
    """
    Convert raw 8-channel ADS1299 data into 6 derived display signals.

    Parameters
    ----------
    raw_uv : np.ndarray, shape (N, 8)
        Physical channel voltages in µV. Column order matches PHYSICAL_CH.

    Returns
    -------
    derived : np.ndarray, shape (N, 6)
        Derived signals in µV. Column order matches DERIVED_LABELS.

    Notes
    -----
    CH7 (powered down) and CH8 (BIAS) are handled separately by the viewer
    and are NOT included here — only the 6 signal rows are returned.
    """
    if raw_uv.ndim != 2 or raw_uv.shape[1] != 8:
        raise ValueError(f"derive_signals expects shape (N, 8), got {raw_uv.shape}")

    eog          = raw_uv[:, 0]                    # CH1
    ecg          = raw_uv[:, 1]                    # CH2 (EMG_far)
    emg          = raw_uv[:, 1] - raw_uv[:, 2]    # CH2 - CH3
    eeg_occip    = raw_uv[:, 3] - raw_uv[:, 5]    # CH4 - CH6
    eeg_central  = raw_uv[:, 4]                    # CH5
    eeg_frontal  = raw_uv[:, 5]                    # CH6

    return np.column_stack([
        eog,
        ecg,
        emg,
        eeg_occip,
        eeg_central,
        eeg_frontal,
    ])


# ---------------------------------------------------------------------------
# EEGProcessor
# ---------------------------------------------------------------------------

class EEGProcessor:
    """
    Stateful IIR filter chain for 6 derived EEG signal rows.

    Filter stages (in order):
      1. 60 Hz IIR notch  (Q=30)         — optional, notch_on flag
      2. 0.5 Hz Butterworth HP, 2nd order — always applied (blocks DC drift)
      3. LP / bandpass stage:
           Full mode  → 40 Hz Butterworth LP, 4th order
           Band mode  → Butterworth bandpass, 4th order (2 per side)

    The zi (initial conditions) arrays are maintained between calls so
    continuous streaming does not produce edge transients at each animation
    frame boundary.
    """

    # All supported band mode names
    BAND_MODES = list(BAND_RANGES.keys())

    def __init__(self, fs: float = 250.0):
        self.fs       = fs
        self.notch_on = True
        self.band_mode = "Full"
        self._build_filters()
        self._reset_zi()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_notch(self, enabled: bool):
        """Enable or disable the 60 Hz notch filter."""
        self.notch_on = enabled
        self._reset_zi()

    def set_band(self, mode: str):
        """
        Switch the LP/bandpass stage.

        Parameters
        ----------
        mode : str
            One of BAND_MODES ('Full', 'Delta', 'Theta', 'Alpha', 'Beta', 'Gamma').
        """
        if mode not in BAND_RANGES:
            raise ValueError(f"Unknown band mode '{mode}'. Choose from {self.BAND_MODES}")
        self.band_mode = mode
        self._reset_zi()

    def reset_state(self):
        """
        Reset all filter initial conditions to zero.
        Call this whenever gain changes or a new stream starts, to avoid
        large transients from mismatched initial conditions.
        """
        self._reset_zi()

    def process(self, derived_uv: np.ndarray) -> np.ndarray:
        """
        Run the filter chain on a batch of derived samples.

        Parameters
        ----------
        derived_uv : np.ndarray, shape (N, 6)
            Derived signal voltages in µV (output of derive_signals).

        Returns
        -------
        filtered : np.ndarray, shape (N, 6)
            Filtered signals in µV.

        Notes
        -----
        Filter state (zi) is updated in place so successive calls are seamless.
        If N < 3 the sosfilt result may be numerically imprecise but state is
        still propagated correctly.
        """
        if derived_uv.ndim != 2 or derived_uv.shape[1] != NUM_DERIVED:
            raise ValueError(
                f"process() expects shape (N, {NUM_DERIVED}), got {derived_uv.shape}"
            )

        out = np.ascontiguousarray(derived_uv)  # (N, 6) C-order copy

        # Each stage is one vectorized sosfilt call over all 6 channels (axis=0).
        # zi shape: (n_sections, 2, NUM_DERIVED) — scipy fills the channel loop in C.
        # This replaces 18 per-channel Python calls with 3 C-level calls.

        # Stage 1: 60 Hz notch
        if self.notch_on:
            out, self._zi_notch = sosfilt(
                self._sos_notch, out, axis=0, zi=self._zi_notch
            )

        # Stage 2: 0.5 Hz HP (always on)
        out, self._zi_hp = sosfilt(
            self._sos_hp, out, axis=0, zi=self._zi_hp
        )

        # Stage 3: LP or bandpass
        sos_lp = self._sos_lp_for_mode(self.band_mode)
        out, self._zi_lp = sosfilt(
            sos_lp, out, axis=0, zi=self._zi_lp
        )

        return out

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_filters(self):
        """Build all SOS filter objects. Called once at __init__."""
        fs = self.fs
        nyq = fs / 2.0

        # --- 60 Hz notch ---
        b_notch, a_notch = iirnotch(60.0, Q=30.0, fs=fs)
        self._sos_notch = tf2sos(b_notch, a_notch)

        # --- 0.5 Hz 2nd-order Butterworth HP ---
        self._sos_hp = butter(2, 0.5 / nyq, btype="high", output="sos")

        # --- 40 Hz 4th-order Butterworth LP (Full mode) ---
        self._sos_lp_full = butter(4, 40.0 / nyq, btype="low", output="sos")

        # --- Bandpass SOS for each non-Full band ---
        self._sos_band = {}
        for name, freqs in BAND_RANGES.items():
            if freqs is None:
                continue  # Full handled above
            lo, hi = freqs
            # Clamp to valid range — lo must be > 0, hi < nyq
            lo = max(lo, 0.1)
            hi = min(hi, nyq - 0.5)
            self._sos_band[name] = butter(
                4, [lo / nyq, hi / nyq], btype="band", output="sos"
            )

    def _sos_lp_for_mode(self, mode: str) -> np.ndarray:
        """Return the appropriate SOS array for the current LP/band stage."""
        if mode == "Full":
            return self._sos_lp_full
        return self._sos_band[mode]

    def _reset_zi(self):
        """
        Reset (zero-initialize) all filter state vectors.
        Vectorized sosfilt needs zi shape (n_sections, 2, NUM_DERIVED).
        """
        def _make_zi(sos):
            # sosfilt_zi returns (n_sections, 2); extend to all channels at once
            template = sosfilt_zi(sos)   # (n_sections, 2)
            return np.zeros((*template.shape, NUM_DERIVED), dtype=np.float64)

        self._zi_notch = _make_zi(self._sos_notch)
        self._zi_hp    = _make_zi(self._sos_hp)
        self._zi_lp    = _make_zi(self._sos_lp_for_mode(self.band_mode))
