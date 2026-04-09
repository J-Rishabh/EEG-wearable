#!/usr/bin/env python3
"""
bci/auditory_oddball.py
=======================
Auditory oddball paradigm for P300 ERP analysis.

Plays a pseudo-random sequence of tones:
  Standard (frequent) : 1000 Hz, 80 ms, p = 0.80
  Deviant  (rare)     : 2000 Hz, 80 ms, p = 0.20

The rare deviant tone elicits a P300 component ~300 ms post-stimulus
in frontal/central electrodes (Fz, Cz, Pz) when the subject silently
counts the deviant tones.

Saves an event-log CSV with absolute wall-clock timestamps for each
stimulus.  Alignment with the EEG recording is automatic — no manual
sync keypress required.

─────────────────────────────────────────────────────────────────────
Sync workflow
─────────────────────────────────────────────────────────────────────
  1. Open eeg_stream_pg.py and press 'r' to start recording.
  2. Run this script and press ENTER when ready — paradigm starts.
  3. When the paradigm finishes, press 's' in the EEG viz to save.
     → This writes recordings/eeg_YYYYMMDD_HHMMSS_meta.json which
       contains rec_start_epoch (the exact time.time() of step 1).

  Offline, the analysis script loads the meta.json and aligns:
      sample_offset = (event_wall_time - rec_start_epoch) * FS
  No manual synchronisation is needed.

─────────────────────────────────────────────────────────────────────
Offline analysis (MNE-Python sketch)
─────────────────────────────────────────────────────────────────────
  import mne, pandas as pd, numpy as np, json, glob, os

  # Auto-find the most recent recording pair
  rec_dir   = "python/recordings"
  meta_file = sorted(glob.glob(f"{rec_dir}/*_meta.json"),
                     key=os.path.getmtime)[-1]
  with open(meta_file) as f:
      meta = json.load(f)

  edf_file  = os.path.join(rec_dir, meta["edf_file"])
  rec_start = meta["rec_start_epoch"]   # time.time() when 'r' was pressed
  FS        = meta["fs"]                # 250

  raw = mne.io.read_raw_edf(edf_file, preload=True)
  ev  = pd.read_csv("bci/events/events_oddball_YYYYMMDD_HHMMSS.csv")

  def make_mne_events(df, event_id):
      samples = ((df.wall_time - rec_start) * FS).round().astype(int).values
      samples = np.clip(samples, 0, meta["n_samples"] - 1)
      return np.column_stack([samples,
                              np.zeros(len(samples), int),
                              np.full(len(samples), event_id)])

  events     = np.vstack([make_mne_events(ev[ev.event == "deviant"],  2),
                           make_mne_events(ev[ev.event == "standard"], 1)])
  event_dict = {"standard": 1, "deviant": 2}

  raw.filter(1., 40.)
  epochs = mne.Epochs(raw, events, event_id=event_dict,
                      tmin=-0.2, tmax=0.8, baseline=(-0.2, 0), preload=True)
  evoked_deviant  = epochs["deviant"].average()
  evoked_standard = epochs["standard"].average()
  evoked_deviant.plot()   # expect P300 peak ~300 ms post-deviant

─────────────────────────────────────────────────────────────────────
Requirements
─────────────────────────────────────────────────────────────────────
  pip install pygame numpy
"""

import os
import sys
import time
import csv
import random
from datetime import datetime

import numpy as np
import pygame

# ── Paradigm parameters ───────────────────────────────────────────────────────

N_TRIALS     = 150       # total tone presentations
P_DEVIANT    = 0.20      # fraction of trials that are deviant
SOA_MS       = 1200      # stimulus onset asynchrony (ms) — time between tone onsets
TONE_DUR_MS  = 80        # duration of each tone (ms)
RAMP_MS      = 10        # linear fade-in / fade-out to avoid clicks (ms)

FREQ_STD     = 1000      # Hz — standard tone
FREQ_DEV     = 2000      # Hz — deviant tone
SAMPLE_RATE  = 44100     # audio sample rate (Hz)
VOLUME       = 0.7       # 0.0 – 1.0

# Output directory for event logs (relative to this script)
EVENT_DIR = os.path.join(os.path.dirname(__file__), "events")


# ── Tone synthesis ─────────────────────────────────────────────────────────────

def _make_tone(freq_hz: int, dur_ms: int, ramp_ms: int,
               sample_rate: int = SAMPLE_RATE, volume: float = VOLUME):
    """Synthesize a pure sine tone with linear fade-in/out as a pygame Sound."""
    n_total = int(sample_rate * dur_ms / 1000)
    n_ramp  = int(sample_rate * ramp_ms / 1000)

    t    = np.linspace(0, dur_ms / 1000, n_total, endpoint=False)
    wave = np.sin(2 * np.pi * freq_hz * t)

    ramp           = np.ones(n_total)
    ramp[:n_ramp]  = np.linspace(0, 1, n_ramp)
    ramp[-n_ramp:] = np.linspace(1, 0, n_ramp)
    wave          *= ramp * volume

    samples = (wave * 32767).astype(np.int16)
    stereo  = np.ascontiguousarray(np.column_stack([samples, samples]))  # (N, 2) stereo
    return pygame.sndarray.make_sound(stereo)


# ── Trial sequence generation ──────────────────────────────────────────────────

def _make_sequence(n_trials: int, p_deviant: float, min_std_between: int = 2) -> list:
    """
    Generate a pseudo-random trial sequence.

    Rules:
      - Global deviant probability ≈ p_deviant.
      - At least min_std_between standards between consecutive deviants
        (avoids refractory-period contamination of the P300).
      - Never start with a deviant (first 2 trials are always standard).
    """
    n_deviant  = max(1, round(n_trials * p_deviant))
    n_standard = n_trials - n_deviant

    seq = ["standard"] * n_standard + ["deviant"] * n_deviant

    while True:
        random.shuffle(seq)
        if seq[0] == "deviant" or seq[1] == "deviant":
            continue
        ok = True
        last_dev = -999
        for i, t in enumerate(seq):
            if t == "deviant":
                if (i - last_dev) <= min_std_between:
                    ok = False
                    break
                last_dev = i
        if ok:
            break

    return seq


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(" Auditory Oddball Paradigm — P300")
    print("=" * 60)
    print(f"  Trials      : {N_TRIALS}  ({int(N_TRIALS * P_DEVIANT)} deviant, "
          f"{int(N_TRIALS * (1 - P_DEVIANT))} standard)")
    print(f"  SOA         : {SOA_MS} ms")
    print(f"  Tone dur    : {TONE_DUR_MS} ms")
    print(f"  Frequencies : std={FREQ_STD} Hz  dev={FREQ_DEV} Hz")
    print(f"  Total time  : ~{N_TRIALS * SOA_MS / 1000:.0f} s  "
          f"({N_TRIALS * SOA_MS / 1000 / 60:.1f} min)")
    print()
    print("Subject instructions:")
    print("  Sit still, eyes open, count the HIGH-pitched beeps silently.")
    print("  Do not respond aloud or press any button.")
    print()
    print("─" * 60)
    print("Workflow:")
    print("  1. Press 'r' in eeg_stream_pg.py to start recording (if not already).")
    print("  2. Press ENTER here — paradigm starts immediately.")
    print("  3. When 'DONE' prints, press 's' in the EEG viz to save.")
    print("     The saved meta.json enables automatic offline alignment.")
    print("─" * 60)
    input("Press ENTER to begin > ")
    print()

    # Init pygame audio — pre_init must be set before pygame.init().
    # Do NOT call pygame.mixer.init() separately; pygame.init() calls it
    # internally using the pre_init settings. Calling mixer.init() then
    # pygame.init() reinitializes the mixer and discards the pre_init params.
    pygame.mixer.pre_init(frequency=SAMPLE_RATE, size=-16, channels=2, buffer=512)
    pygame.init()

    if not pygame.mixer.get_init():
        sys.exit(
            "[ERROR] pygame mixer failed to initialize.\n"
            "  • Check that an audio output device is available and not in use.\n"
            "  • Try a different buffer size (e.g. 1024) at the top of the script."
        )

    tone_std = _make_tone(FREQ_STD, TONE_DUR_MS, RAMP_MS)
    tone_dev = _make_tone(FREQ_DEV, TONE_DUR_MS, RAMP_MS)

    sequence = _make_sequence(N_TRIALS, P_DEVIANT)

    os.makedirs(EVENT_DIR, exist_ok=True)
    # Use the time of first stimulus as the filename timestamp
    ts_start = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(EVENT_DIR, f"events_oddball_{ts_start}.csv")

    n_deviant_seen = 0

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["wall_time", "trial", "event", "freq_hz"])
        print(f"[LOG] Saving events to {csv_path}")
        print("Running paradigm...\n")

        t_start = time.time()
        t_next  = time.perf_counter()  # high-res scheduler

        for i, trial_type in enumerate(sequence):
            # Busy-wait until the scheduled SOA boundary (avoids sleep() drift)
            while time.perf_counter() < t_next:
                pass

            t_stim = time.time()  # absolute wall-clock for this stimulus

            if trial_type == "deviant":
                tone_dev.play()
                n_deviant_seen += 1
                freq = FREQ_DEV
            else:
                tone_std.play()
                freq = FREQ_STD

            writer.writerow([t_stim, i + 1, trial_type, freq])

            if (i + 1) % 10 == 0 or i == 0:
                elapsed = t_stim - t_start
                print(f"  Trial {i+1:3d}/{N_TRIALS}  "
                      f"type={trial_type:<8s}  "
                      f"deviant_count={n_deviant_seen:3d}  "
                      f"elapsed={elapsed:.1f}s")

            t_next += SOA_MS / 1000.0

        t_end = time.time()
        writer.writerow([t_end, N_TRIALS + 1, "END", ""])

    print()
    print("=" * 60)
    print(" PARADIGM COMPLETE")
    print(f"  Deviant count : {n_deviant_seen}")
    print(f"  Duration      : {t_end - t_start:.0f}s")
    print(f"  Event log     : {csv_path}")
    print()
    print("  → Press 's' in the EEG viz now to stop + save the EDF.")
    print("  → The meta.json written alongside the EDF will be used for")
    print("    automatic alignment in offline analysis.")
    print("=" * 60)

    pygame.quit()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[ABORT] Paradigm interrupted by user.")
        pygame.quit()
        sys.exit(1)
