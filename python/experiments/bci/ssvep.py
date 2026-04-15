#!/usr/bin/env python3
"""
bci/ssvep.py
============
SSVEP (Steady-State Visual Evoked Potential) paradigm.

Presents a full-screen flickering checkerboard at target frequencies.
The visual cortex phase-locks to the flicker, producing a narrow
spectral peak in occipital EEG at exactly the stimulus frequency.

─────────────────────────────────────────────────────────────────────
Frequency selection
─────────────────────────────────────────────────────────────────────
At a 60 Hz monitor, phase-accurate flicker requires integer divisors
of 60:
      60 / 10 =  6 Hz   ← theta/alpha boundary
      60 /  6 = 10 Hz   ← alpha band
      60 /  5 = 12 Hz   ← alpha band
      60 /  4 = 15 Hz   ← alpha/beta boundary

All four default targets divide 60 exactly — no timing jitter.

─────────────────────────────────────────────────────────────────────
Protocol per run
─────────────────────────────────────────────────────────────────────
  For each target frequency (randomised order per run):
    3 s  — REST (gray, fixation cross)
    12 s — FLICKER
  Total: 4 freqs × 15 s × RUNS = 60 s × RUNS (~3 min at RUNS=3)

─────────────────────────────────────────────────────────────────────
Sync workflow
─────────────────────────────────────────────────────────────────────
  1. Press 'r' in eeg_stream_pg.py to start recording.
  2. Run this script and press ENTER to begin.
  3. When all runs finish, press 's' in the EEG viz to save.
     → recordings/eeg_YYYYMMDD_HHMMSS_meta.json is written with
       rec_start_epoch (exact time.time() when 'r' was pressed).

  Offline alignment is fully automatic — no manual sync needed:
      sample_offset = (event_wall_time - rec_start_epoch) * FS

─────────────────────────────────────────────────────────────────────
Offline analysis sketch (MNE-Python + scipy)
─────────────────────────────────────────────────────────────────────
  import mne, pandas as pd, numpy as np, json, glob, os
  from scipy.signal import welch

  rec_dir   = "python/recordings"
  meta_file = sorted(glob.glob(f"{rec_dir}/*_meta.json"),
                     key=os.path.getmtime)[-1]
  with open(meta_file) as f:
      meta = json.load(f)

  raw       = mne.io.read_raw_edf(os.path.join(rec_dir, meta["edf_file"]),
                                   preload=True)
  rec_start = meta["rec_start_epoch"]
  FS        = meta["fs"]

  ev = pd.read_csv("bci/events/events_ssvep_YYYYMMDD_HHMMSS.csv")
  flicker = ev[ev.event == "FLICKER_START"]

  for _, row in flicker.iterrows():
      s0   = int((row.wall_time - rec_start) * FS)
      s1   = s0 + int(12.0 * FS)          # 12 s epoch
      data = raw.get_data(start=s0, stop=s1)[3]  # e.g. EEG_L1 (index 3)
      f, psd = welch(data, fs=FS, nperseg=FS * 4)
      target  = row.freq_hz
      idx     = np.argmin(np.abs(f - target))
      print(f"  {target} Hz  PSD = {psd[idx]:.3e} V²/Hz")

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

TARGET_FREQS   = [6, 10, 12, 15]   # Hz — must divide MONITOR_HZ evenly
MONITOR_HZ     = 60                # display refresh rate
REST_SEC       = 3.0               # rest between epochs
FLICKER_SEC    = 15.0              # flicker duration per epoch
WARMUP_SEC     = 2.0               # first N seconds excluded from analysis (visual cortex entrainment transient)
RUNS           = 6                 # cycles through all frequencies

BOARD_COLS     = 8
BOARD_ROWS     = 8
COLOR_A        = (255, 255, 255)
COLOR_B        = (0,   0,   0)
COLOR_REST     = (128, 128, 128)
COLOR_TEXT     = (220, 220, 50)

EVENT_DIR = os.path.join(os.path.dirname(__file__), "events")


# ── Drawing helpers ────────────────────────────────────────────────────────────

def _draw_checkerboard(surface, phase: int):
    """Draw 8×8 checkerboard; phase 0/1 inverts the pattern each toggle."""
    w, h  = surface.get_size()
    sq_w  = w // BOARD_COLS
    sq_h  = h // BOARD_ROWS
    for row in range(BOARD_ROWS):
        for col in range(BOARD_COLS):
            color = COLOR_A if (row + col + phase) % 2 == 0 else COLOR_B
            pygame.draw.rect(surface, color,
                             pygame.Rect(col * sq_w, row * sq_h, sq_w, sq_h))


def _draw_fixation(surface):
    cx, cy = surface.get_width() // 2, surface.get_height() // 2
    pygame.draw.line(surface, (255, 0, 0), (cx - 15, cy), (cx + 15, cy), 3)
    pygame.draw.line(surface, (255, 0, 0), (cx, cy - 15), (cx, cy + 15), 3)


def _draw_rest(surface, msg: str = "REST — relax and fixate", countdown: float = None):
    surface.fill(COLOR_REST)
    font  = pygame.font.SysFont("monospace", 28)
    label = font.render(msg, True, COLOR_TEXT)
    rect  = label.get_rect(center=(surface.get_width() // 2,
                                   surface.get_height() // 2 + 60))
    surface.blit(label, rect)
    if countdown is not None:
        cd_font  = pygame.font.SysFont("monospace", 48)
        cd_label = cd_font.render(f"{countdown:.1f}", True, (200, 200, 200))
        cd_rect  = cd_label.get_rect(center=(surface.get_width() // 2,
                                              surface.get_height() // 2 + 120))
        surface.blit(cd_label, cd_rect)
    _draw_fixation(surface)


def _draw_flicker_label(surface, freq_hz, elapsed, total):
    font  = pygame.font.SysFont("monospace", 20)
    label = font.render(f"{freq_hz} Hz  [{elapsed:.1f}/{total:.0f}s]",
                        True, (200, 200, 0))
    surface.blit(label, (10, 10))


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(" SSVEP Paradigm")
    print("=" * 60)
    print(f"  Frequencies : {TARGET_FREQS} Hz")
    print(f"  Runs        : {RUNS}")
    print(f"  Epoch       : {REST_SEC}s rest + {FLICKER_SEC}s flicker")
    est_sec = RUNS * len(TARGET_FREQS) * (REST_SEC + FLICKER_SEC)
    print(f"  Total time  : ~{est_sec:.0f} s  ({est_sec/60:.1f} min)")
    print()
    print("Subject instructions:")
    print("  Fix your gaze on the center cross throughout each block.")
    print("  Remain still. Avoid blinking during flicker epochs.")
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

    # Init pygame fullscreen
    pygame.init()
    info   = pygame.display.Info()
    screen = pygame.display.set_mode((info.current_w, info.current_h),
                                     pygame.FULLSCREEN | pygame.HWSURFACE | pygame.DOUBLEBUF)
    pygame.display.set_caption("SSVEP")
    clock  = pygame.time.Clock()

    # Warn for non-exact divisors (shouldn't occur with default TARGET_FREQS)
    for f in TARGET_FREQS:
        fph = MONITOR_HZ / (2 * f)
        if not fph.is_integer():
            print(f"  [WARN] {f} Hz: {fph:.2f} frames/half-cycle — small timing jitter")

    os.makedirs(EVENT_DIR, exist_ok=True)
    ts_start = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(EVENT_DIR, f"events_ssvep_{ts_start}.csv")

    def pump():
        """Drain pygame event queue. Returns True if ESC pressed."""
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return True
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                return True
        return False

    aborted = False

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["wall_time", "run", "freq_hz", "event", "note"])
        print(f"[LOG] Saving events to {csv_path}")
        print("      Press ESC to abort early.\n")

        for run_idx in range(1, RUNS + 1):
            run_order = TARGET_FREQS[:]
            random.shuffle(run_order)
            print(f"Run {run_idx}/{RUNS}  order={run_order}")

            for freq_hz in run_order:
                if aborted:
                    break

                frames_per_toggle = MONITOR_HZ / (2 * freq_hz)  # frames per half-cycle

                # ── REST ─────────────────────────────────────────────────────
                t_rest = time.time()
                writer.writerow([t_rest, run_idx, freq_hz, "REST_START", ""])

                deadline = t_rest + REST_SEC
                while time.time() < deadline:
                    if pump():
                        aborted = True
                        break
                    remaining = deadline - time.time()
                    _draw_rest(screen, f"REST  —  next: {freq_hz} Hz",
                               countdown=max(0.0, remaining))
                    pygame.display.flip()
                    clock.tick(MONITOR_HZ)

                if aborted:
                    break

                # ── FLICKER ──────────────────────────────────────────────────
                t_flicker = time.time()
                writer.writerow([t_flicker, run_idx, freq_hz, "FLICKER_START", ""])
                print(f"  [{run_idx}/{RUNS}] {freq_hz} Hz  start  t={t_flicker:.3f}")

                # Log when the entrainment transient is over — analysis starts here
                t_analysis = t_flicker + WARMUP_SEC
                warmup_logged = False

                phase        = 0
                accum        = 0.0
                frame_count  = 0
                deadline     = t_flicker + FLICKER_SEC

                while time.time() < deadline:
                    if pump():
                        aborted = True
                        break
                    now = time.time()
                    if not warmup_logged and now >= t_analysis:
                        writer.writerow([now, run_idx, freq_hz, "ANALYSIS_START",
                                         f"warmup={WARMUP_SEC}s"])
                        warmup_logged = True
                    accum += 1.0
                    if accum >= frames_per_toggle:
                        accum -= frames_per_toggle
                        phase  = 1 - phase
                    _draw_checkerboard(screen, phase)
                    _draw_fixation(screen)
                    _draw_flicker_label(screen, freq_hz,
                                        now - t_flicker, FLICKER_SEC)
                    pygame.display.flip()
                    clock.tick(MONITOR_HZ)
                    frame_count += 1

                t_end_flicker = time.time()
                actual_hz     = frame_count / (t_end_flicker - t_flicker) / 2
                writer.writerow([t_end_flicker, run_idx, freq_hz, "FLICKER_END",
                                  f"frames={frame_count} actual_hz={actual_hz:.2f}"])
                print(f"         end    frames={frame_count}  "
                      f"actual={actual_hz:.2f} Hz")

            if aborted:
                break

        if not aborted:
            _draw_rest(screen, "Done — please hold still")
            pygame.display.flip()
            t_paradigm_end = time.time()
            writer.writerow([t_paradigm_end, RUNS, "", "END", ""])
            time.sleep(2.0)

    pygame.quit()

    status = "ABORTED" if aborted else "COMPLETE"
    print()
    print("=" * 60)
    print(f" PARADIGM {status}")
    print(f"  Event log : {csv_path}")
    print()
    print("  → Press 's' in the EEG viz to stop + save the EDF.")
    print("  → The meta.json written alongside will auto-align events.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pygame.quit()
        print("\n[ABORT] Interrupted.")
        sys.exit(1)
