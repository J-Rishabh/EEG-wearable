#!/usr/bin/env python3
"""
bci/auditory_oddball.py  —  Auditory oddball paradigm for P300 ERP analysis.

Standard (frequent) : 1000 Hz, 80 ms, p = 0.80
Deviant  (rare)     : 2000 Hz, 80 ms, p = 0.20

Uses pygame.mixer for stable audio timing. Tones are pre-generated as numpy
sine waves and buffered before the run starts, so play() returns in <1 ms with
consistent latency (determined by the mixer buffer, ~6 ms at buffer=256).
This eliminates the trial-to-trial jitter from winsound.Beep() which routed
through the Windows audio scheduler and could vary by ±50 ms.

Sync workflow
─────────────────────────────────────────────────────────────────────
  1. Press 'r' in eeg_stream_pg.py to start recording.
  2. Run this script, press ENTER — paradigm starts.
  3. When DONE prints, press 's' in the EEG viz to save.
     Alignment:  sample_offset = (event_wall_time - rec_start_epoch) * FS
"""

import os
import sys
import csv
import time
import random
import numpy as np
import pygame
import pygame.sndarray
from datetime import datetime

# ── Parameters ────────────────────────────────────────────────────────────────

N_TRIALS    = 500
P_DEVIANT   = 0.20
SOA_MS      = 1200   # ms between tone onsets
TONE_DUR_MS = 150    # ms

FREQ_STD    = 1000   # Hz
FREQ_DEV    = 2000   # Hz

# pygame mixer settings — small buffer = low, consistent latency (~6 ms at 256)
SAMPLE_RATE = 44100
MIXER_BUFFER = 256   # samples; latency = MIXER_BUFFER / SAMPLE_RATE ≈ 5.8 ms

EVENT_DIR = os.path.join(os.path.dirname(__file__), "events")


# ── Tone generation ────────────────────────────────────────────────────────────

def _make_tone(freq_hz, duration_ms, amplitude=0.8):
    """
    Generate a sine-wave tone as a pygame Sound object.
    10 ms cosine ramp at start/end to eliminate clicks.
    """
    n = int(SAMPLE_RATE * duration_ms / 1000)
    t = np.linspace(0, duration_ms / 1000.0, n, endpoint=False)
    wave = np.sin(2.0 * np.pi * freq_hz * t)

    # 10 ms cosine onset/offset ramp — eliminates click transients
    ramp_n = min(int(SAMPLE_RATE * 0.010), n // 4)
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, ramp_n))
    wave[:ramp_n]  *= ramp
    wave[-ramp_n:] *= ramp[::-1]

    wave = (wave * amplitude * 32767).astype(np.int16)
    stereo = np.column_stack([wave, wave])   # pygame mixer expects stereo
    return pygame.sndarray.make_sound(stereo)


# ── Sequence ──────────────────────────────────────────────────────────────────

def _make_sequence(n_trials, p_deviant, min_std_between=2):
    """Build sequence by placing deviants into shuffled slots — always terminates."""
    n_dev = max(1, round(n_trials * p_deviant))

    # Create candidate positions for deviants: every (min_std_between+1)-th slot,
    # starting after the first 2 (which must be standards).
    step = min_std_between + 1          # e.g. 3 — guarantees spacing
    slots = list(range(2, n_trials, step))
    random.shuffle(slots)
    dev_positions = set(slots[:n_dev])  # pick first n_dev shuffled slots

    seq = ["dev" if i in dev_positions else "std" for i in range(n_trials)]
    return seq


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Init pygame mixer first — small buffer for consistent latency
    pygame.mixer.pre_init(frequency=SAMPLE_RATE, size=-16, channels=2,
                          buffer=MIXER_BUFFER)
    pygame.init()

    # Pre-generate tones before the run so there's no generation delay mid-trial
    print("Generating tones...", flush=True)
    snd_std = _make_tone(FREQ_STD, TONE_DUR_MS)
    snd_dev = _make_tone(FREQ_DEV, TONE_DUR_MS)

    est_min = N_TRIALS * SOA_MS / 1000 / 60
    print("=" * 55)
    print(" Auditory Oddball — P300")
    print(f"  {N_TRIALS} trials  |  SOA {SOA_MS} ms  |  ~{est_min:.1f} min")
    print(f"  STD {FREQ_STD} Hz  |  DEV {FREQ_DEV} Hz  |  dur {TONE_DUR_MS} ms")
    print(f"  Expected deviants: ~{round(N_TRIALS * P_DEVIANT)}")
    print(f"  Mixer: {SAMPLE_RATE} Hz, buffer {MIXER_BUFFER} samples "
          f"(~{1000*MIXER_BUFFER/SAMPLE_RATE:.1f} ms latency)")
    print("=" * 55)
    print("  Count the HIGH-pitched beeps silently.")
    print()
    print("  1. Press 'r' in eeg_stream_pg.py to start recording.")
    print("  2. Press ENTER here to begin.")
    print("  3. Press 's' in EEG viz when done.")
    print("─" * 55)

    input("Press ENTER to begin > ")

    # Sanity check — long tones so they are unmistakably audible
    print(f"[AUDIO] Test 1/2: {FREQ_STD} Hz (low) ...")
    _make_tone(FREQ_STD, 600).play()
    time.sleep(0.8)
    print(f"[AUDIO] Test 2/2: {FREQ_DEV} Hz (high) ...")
    _make_tone(FREQ_DEV, 600).play()
    time.sleep(0.8)
    input("Heard a LOW then a HIGH beep? ENTER to run, Ctrl-C to abort > ")

    print("Generating sequence...", flush=True)
    seq = _make_sequence(N_TRIALS, P_DEVIANT)
    print(f"Sequence ready: {len(seq)} trials", flush=True)

    os.makedirs(EVENT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(EVENT_DIR, f"events_oddball_{ts}.csv")

    n_dev_seen = 0
    t_start    = time.time()
    t_next     = time.perf_counter()

    with open(csv_path, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["wall_time", "trial", "event", "freq_hz"])
        print(f"\n[LOG] {csv_path}\nRunning...\n")

        for i, s in enumerate(seq):
            # Wait until SOA boundary (perf_counter for precision)
            wait = t_next - time.perf_counter()
            if wait > 0:
                time.sleep(wait)

            # Timestamp immediately before play() — pygame buffers are pre-loaded
            # so play() returns in <1 ms; jitter from here to sound-at-speaker
            # is the fixed mixer buffer latency (~5.8 ms), not variable OS scheduling
            t_stim = time.time()

            if s == "dev":
                snd_dev.play()
                n_dev_seen += 1
                print(f"  [{i+1:3d}] DEVIANT  (total dev: {n_dev_seen})")
            else:
                snd_std.play()
                print(f"  [{i+1:3d}] standard")

            wr.writerow([t_stim, i + 1, "deviant" if s == "dev" else "standard",
                         FREQ_DEV if s == "dev" else FREQ_STD])

            t_next += SOA_MS / 1000.0

        wr.writerow([time.time(), N_TRIALS + 1, "END", ""])

    pygame.quit()
    print(f"\nDONE — {n_dev_seen} deviants in {time.time()-t_start:.0f}s")
    print("→ Press 's' in EEG viz to save.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pygame.quit()
        print("\n[ABORT]")
        sys.exit(1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        pygame.quit()
        sys.exit(1)
