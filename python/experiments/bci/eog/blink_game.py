"""
blink_game.py — Flappy Bird controlled by eye blinks
=====================================================
Blink to flap.  Pass through the gaps between pipes.
Hit a pipe, the ceiling, or the floor → game over.

Data recording
--------------
Press R (or use --record) to start/stop recording.
Saves to ../sessions/session_YYYYMMDD_HHMMSS.csv with columns:
  time_s, eeg_amplitude_uv, eeg_blink, eeg_railed, cv_ear, cv_blink, bird_y,
  game_state, score

Then run:
  python analyze_session.py latest

Channel selection
-----------------
  --channel 0   CH1  EOG         (default)
  --channel 4   CH5  EEG central (often cleaner for blinks)

Controls
--------
  SPACE / UP    keyboard flap (fallback / no-hardware mode)
  R             toggle recording
  --ble         connect to EEG Wearable over BLE
  --cv          enable webcam CV ground truth (requires mediapipe)
  --record      start recording immediately on launch
  --channel N   ADS1299 channel 0-7 (default 0)
  --threshold T blink threshold in µV (default 150)
  ESC / Q       quit

Requirements
------------
    pip install pygame numpy scipy bleak
    pip install mediapipe opencv-python  # only for --cv
"""

import pygame
import sys
import os
import math
import random
import csv
import time as _time
import numpy as np
from pathlib import Path

# ── path setup ────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..", "..", "..", "..")   # → python/
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)

SESSIONS_DIR = Path(_HERE).parent / 'sessions'

# ── config ────────────────────────────────────────────────────────────────────

WIDTH,  HEIGHT   = 480, 640
SIGNAL_STRIP_H   = 70
GAME_H           = HEIGHT - SIGNAL_STRIP_H
FPS              = 60

# Bird
BIRD_X    = 120
BIRD_R    = 18
GRAVITY   = 1100.0
FLAP_VEL  = -390.0
MAX_FALL  = 700.0

# Pipes
PIPE_W      = 68
PIPE_GAP    = 200
PIPE_SPEED  = 200.0
PIPE_SPAWN  = 300

# Colors
C_SKY_TOP   = ( 80, 180, 230)
C_SKY_BOT   = (150, 220, 255)
C_GROUND    = ( 90, 160,  40)
C_GROUND_D  = ( 70, 120,  30)
C_PIPE      = ( 80, 180,  60)
C_PIPE_CAP  = ( 60, 160,  50)
C_WHITE     = (255, 255, 255)
C_HUD       = (255, 255, 255)
C_DIM       = (160, 160, 160)
C_REC       = (220,  40,  40)
C_SIGNAL_BG = ( 12,  14,  18)
C_SIGNAL_FG = ( 80, 220,  80)
C_BLINK_FLG = (255,  80,  80)
C_RAIL_BG   = ( 55,  28,  14)
C_RAIL_TXT  = (255, 160,  60)

GROUND_Y    = GAME_H - 56


# ── drawing helpers ───────────────────────────────────────────────────────────

def lerp_color(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw_sky(surf):
    for y in range(GAME_H):
        t = y / max(1, GAME_H)
        pygame.draw.line(surf, lerp_color(C_SKY_TOP, C_SKY_BOT, t),
                         (0, y), (WIDTH, y))


def draw_ground(surf, scroll):
    pygame.draw.rect(surf, C_GROUND_D, (0, GROUND_Y, WIDTH, GAME_H - GROUND_Y))
    pygame.draw.rect(surf, C_GROUND,   (0, GROUND_Y, WIDTH, 18))
    spacing = 40
    offset  = int(scroll) % spacing
    x = -offset
    while x < WIDTH:
        pygame.draw.rect(surf, (60, 150, 30), (x, GROUND_Y - 4, 8, 7),
                         border_radius=3)
        x += spacing


def draw_pipe(surf, pipe):
    gap_top = pipe['gap_top']
    gap_bot = gap_top + PIPE_GAP
    x       = int(pipe['x'])

    # Top pipe
    if gap_top > 0:
        pygame.draw.rect(surf, C_PIPE,
                         pygame.Rect(x + 4, 0, PIPE_W - 8, max(0, gap_top - 14)))
        pygame.draw.rect(surf, lerp_color(C_PIPE, C_WHITE, 0.25),
                         pygame.Rect(x + 5, 0, 6, max(0, gap_top - 14)))
        pygame.draw.rect(surf, C_PIPE_CAP,
                         pygame.Rect(x, gap_top - 14, PIPE_W, 14), border_radius=4)
        pygame.draw.rect(surf, lerp_color(C_PIPE_CAP, C_WHITE, 0.3),
                         pygame.Rect(x + 2, gap_top - 14, 8, 14), border_radius=2)

    # Bottom pipe
    bot_h = GROUND_Y - gap_bot
    if bot_h > 0:
        pygame.draw.rect(surf, C_PIPE_CAP,
                         pygame.Rect(x, gap_bot, PIPE_W, 14), border_radius=4)
        pygame.draw.rect(surf, lerp_color(C_PIPE_CAP, C_WHITE, 0.3),
                         pygame.Rect(x + 2, gap_bot, 8, 14), border_radius=2)
        pygame.draw.rect(surf, C_PIPE,
                         pygame.Rect(x + 4, gap_bot + 14, PIPE_W - 8, bot_h - 14))
        pygame.draw.rect(surf, lerp_color(C_PIPE, C_WHITE, 0.25),
                         pygame.Rect(x + 5, gap_bot + 14, 6, bot_h - 14))


def draw_bird(surf, x, y, angle):
    r = BIRD_R
    pygame.draw.circle(surf, (255, 210, 40), (x, y), r)
    pygame.draw.circle(surf, (255, 180, 20), (x + 4, y + 4), r - 4)
    wing_pts = []
    for a in range(-30, 40, 5):
        rad = math.radians(a + 160)
        wing_pts.append((x + int((r - 3) * math.cos(rad)),
                         y + int((r - 3) * math.sin(rad))))
    if len(wing_pts) > 1:
        pygame.draw.lines(surf, (220, 140, 10), False, wing_pts, 4)
    pygame.draw.circle(surf, C_WHITE, (x + 8, y - 5), 6)
    pygame.draw.circle(surf, (20, 20, 20), (x + 9, y - 5), 3)
    pygame.draw.polygon(surf, (255, 130, 20),
                        [(x + r - 2, y - 1), (x + r + 10, y + 1), (x + r - 2, y + 5)])


def draw_signal_strip(surf, signal, threshold_uv, blink_active, channel, strip_y,
                      channel_railed=False):
    if channel_railed:
        bg = C_RAIL_BG
    else:
        bg = (40, 10, 10) if blink_active else C_SIGNAL_BG
    pygame.draw.rect(surf, bg, (0, strip_y, WIDTH, SIGNAL_STRIP_H))
    pygame.draw.line(surf, (40, 40, 50), (0, strip_y), (WIDTH, strip_y), 1)

    font = pygame.font.SysFont("monospace", 11)
    rail_note = "   SAT/RAIL" if channel_railed else ""
    lbl  = font.render(
        f"CH{channel+1}  thr={threshold_uv:.0f} µV"
        + ("   *** BLINK ***" if blink_active else "")
        + rail_note,
        True, (C_RAIL_TXT if channel_railed else (C_BLINK_FLG if blink_active else C_DIM)))
    surf.blit(lbl, (6, strip_y + 4))

    if len(signal) < 2:
        return

    plot_y0 = strip_y + 18
    plot_h  = SIGNAL_STRIP_H - 22
    mid_y   = plot_y0 + plot_h // 2
    scale   = (plot_h / 2) / max(threshold_uv * 2, 1.0)
    th_px   = int(threshold_uv * scale)

    pygame.draw.line(surf, (80, 30, 30),
                     (0, mid_y - th_px), (WIDTH, mid_y - th_px), 1)
    pygame.draw.line(surf, (80, 30, 30),
                     (0, mid_y + th_px), (WIDTH, mid_y + th_px), 1)

    n   = len(signal)
    idx = np.linspace(0, n - 1, WIDTH).astype(int) if n > WIDTH else np.arange(n)
    sig = signal[idx]
    pts = []
    for i, s in enumerate(sig):
        px = int(i * WIDTH / max(len(sig) - 1, 1))
        py = int(mid_y - np.clip(s * scale, -plot_h // 2, plot_h // 2))
        pts.append((px, py))
    if len(pts) > 1:
        line_col = C_RAIL_TXT if channel_railed else (
            C_BLINK_FLG if blink_active else C_SIGNAL_FG)
        pygame.draw.lines(surf, line_col, False, pts, 1)


# ── game ──────────────────────────────────────────────────────────────────────

class FlappyBlink:

    WAITING = "waiting"
    PLAYING = "playing"
    DEAD    = "dead"

    def __init__(self, channel=0, threshold_uv=150.0):
        pygame.init()
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption("Flappy Blink — EEG Wearable")
        self.clock   = pygame.time.Clock()
        self.font_s  = pygame.font.SysFont("monospace", 14)
        self.font_m  = pygame.font.SysFont("monospace", 20, bold=True)
        self.font_l  = pygame.font.SysFont("monospace", 52, bold=True)
        self.font_sc = pygame.font.SysFont("monospace", 48, bold=True)

        self.channel      = channel
        self.threshold_uv = threshold_uv

        self._blink_source   = None
        self._cv_detector    = None
        self._last_signal    = np.zeros(0)
        self._last_amplitude = 0.0
        self._ble_channel_railed = False  # BLE: selected channel near ADC rail (updated from callback)

        # Recording state
        self._recording  = False
        self._rec_buf    = []
        self._rec_start  = 0.0

        self._reset()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_blink_source(self, cb):
        """cb() → (blink: bool, signal: np.ndarray, amplitude: float)"""
        self._blink_source = cb

    def set_cv_detector(self, det):
        """Pass a CVBlinkDetector instance for ground-truth recording."""
        self._cv_detector = det

    def start_recording(self):
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        self._rec_buf   = []
        self._rec_start = _time.time()
        self._recording = True
        print(f"[REC] Recording started → {SESSIONS_DIR}")

    def stop_recording(self):
        self._recording = False
        if not self._rec_buf:
            return
        ts   = _time.strftime('%Y%m%d_%H%M%S', _time.localtime(self._rec_start))
        path = SESSIONS_DIR / f'session_{ts}.csv'
        fields = ['time_s', 'eeg_amplitude_uv', 'eeg_blink', 'eeg_railed',
                  'cv_ear', 'cv_blink', 'bird_y', 'game_state', 'score']
        with open(path, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(self._rec_buf)
        print(f"[REC] Saved {len(self._rec_buf)} frames → {path}")
        self._rec_buf = []

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reset(self):
        self.state   = self.WAITING
        self.score   = 0
        self.best    = getattr(self, 'best', 0)
        self.bird_y  = GAME_H / 2
        self.bird_vy = 0.0
        self.bird_ang = 0.0
        self.pipes   = []
        self.ground_scroll = 0.0
        self._blink_flash  = 0.0
        self._cv_flash     = 0.0
        self._spawn_pipe(WIDTH + 60)

    def _spawn_pipe(self, x=None):
        margin  = 80
        gap_top = random.randint(margin, GROUND_Y - PIPE_GAP - margin)
        self.pipes.append({
            'x':      float(x if x is not None else WIDTH + PIPE_W),
            'gap_top': gap_top,
            'scored':  False,
        })

    def _get_blink(self):
        if self._blink_source:
            result = self._blink_source()
            if len(result) == 3:
                blink, signal, amp = result
                self._last_amplitude = float(amp)
            else:
                blink, signal = result
            if signal is not None and len(signal) > 0:
                self._last_signal = signal
            return blink
        return False

    def _bird_rect(self):
        r = BIRD_R - 4
        return pygame.Rect(BIRD_X - r, int(self.bird_y) - r, r * 2, r * 2)

    def _pipe_rects(self, pipe):
        x, gt = int(pipe['x']), pipe['gap_top']
        return (pygame.Rect(x, 0, PIPE_W, gt),
                pygame.Rect(x, gt + PIPE_GAP, PIPE_W, GROUND_Y - gt - PIPE_GAP))

    def _record_frame(self, eeg_blink, cv_blink, cv_ear):
        if not self._recording:
            return
        self._rec_buf.append({
            'time_s':           round(_time.time() - self._rec_start, 4),
            'eeg_amplitude_uv': round(self._last_amplitude, 4),
            'eeg_blink':        int(eeg_blink),
            'eeg_railed':       int(self._ble_channel_railed),
            'cv_ear':           round(float(cv_ear), 5) if not math.isnan(cv_ear) else '',
            'cv_blink':         int(cv_blink),
            'bird_y':           round(self.bird_y, 2),
            'game_state':       self.state,
            'score':            self.score,
        })

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, dt, flap_key=False):
        eeg_blink = self._get_blink() or flap_key

        # CV ground truth
        cv_blink = False
        cv_ear   = float('nan')
        if self._cv_detector:
            cv_blink = self._cv_detector.pop_blink()
            cv_ear   = self._cv_detector.ear
            if cv_blink:
                self._cv_flash = 0.4

        # Combined blink for game (EEG or keyboard; CV is ground truth only)
        blink = eeg_blink

        if blink:
            self._blink_flash = 0.15
        self._blink_flash = max(0.0, self._blink_flash - dt)
        self._cv_flash    = max(0.0, self._cv_flash    - dt)

        self._record_frame(eeg_blink, cv_blink, cv_ear)

        if self.state == self.WAITING:
            self.bird_y   = GAME_H / 2 + 12 * math.sin(pygame.time.get_ticks() / 300)
            self.bird_ang = 0.0
            self.ground_scroll += PIPE_SPEED * dt
            if blink:
                self.state   = self.PLAYING
                self.bird_vy = FLAP_VEL
            return

        if self.state == self.DEAD:
            self.bird_vy  = min(self.bird_vy + GRAVITY * dt, MAX_FALL)
            self.bird_y  += self.bird_vy * dt
            self.bird_ang = 90.0
            self.bird_y   = min(self.bird_y, float(GROUND_Y - BIRD_R))
            if blink:
                self._reset()
                self.state   = self.PLAYING
                self.bird_vy = FLAP_VEL
            return

        # ---- Playing ----
        if blink:
            self.bird_vy = FLAP_VEL
        self.bird_vy  = min(self.bird_vy + GRAVITY * dt, MAX_FALL)
        self.bird_y  += self.bird_vy * dt

        target_ang = float(np.clip(self.bird_vy * 0.08, -30, 90))
        self.bird_ang += (target_ang - self.bird_ang) * min(1.0, 10.0 * dt)

        self.ground_scroll += PIPE_SPEED * dt

        for p in self.pipes:
            p['x'] -= PIPE_SPEED * dt

        if not self.pipes or self.pipes[-1]['x'] < WIDTH - PIPE_SPAWN:
            self._spawn_pipe()

        for p in self.pipes:
            if not p['scored'] and p['x'] + PIPE_W < BIRD_X:
                self.score += 1
                self.best   = max(self.best, self.score)
                p['scored'] = True

        self.pipes = [p for p in self.pipes if p['x'] > -PIPE_W - 10]

        br  = self._bird_rect()
        hit = (self.bird_y - BIRD_R < 0 or self.bird_y + BIRD_R > GROUND_Y)
        if not hit:
            for p in self.pipes:
                tr, bo = self._pipe_rects(p)
                if br.colliderect(tr) or br.colliderect(bo):
                    hit = True
                    break

        if hit:
            self.state = self.DEAD

    # ------------------------------------------------------------------
    # Draw
    # ------------------------------------------------------------------

    def draw(self):
        surf = self.screen
        draw_sky(surf)
        for p in self.pipes:
            draw_pipe(surf, p)
        draw_ground(surf, self.ground_scroll)
        draw_bird(surf, BIRD_X, int(self.bird_y), self.bird_ang)

        strip_y = HEIGHT - SIGNAL_STRIP_H
        draw_signal_strip(surf, self._last_signal, self.threshold_uv,
                          self._blink_flash > 0, self.channel, strip_y,
                          channel_railed=self._ble_channel_railed)

        # Score
        sc_s  = self.font_sc.render(str(self.score), True, C_WHITE)
        sc_sh = self.font_sc.render(str(self.score), True, (0, 0, 0))
        surf.blit(sc_sh, (WIDTH // 2 - sc_s.get_width() // 2 + 2, 32))
        surf.blit(sc_s,  (WIDTH // 2 - sc_s.get_width() // 2,     30))

        # Source badge
        src = "BLE" if self._blink_source else "KEYBOARD"
        col = (80, 220, 80) if self._blink_source else C_DIM
        surf.blit(self.font_s.render(f"[ {src} | CH{self.channel+1} ]", True, col),
                  (6, 6))

        # Recording indicator
        if self._recording:
            rec_txt = self.font_s.render(
                f"● REC  {_time.time() - self._rec_start:.0f}s", True, C_REC)
            surf.blit(rec_txt, (WIDTH - rec_txt.get_width() - 8, 6))
        else:
            surf.blit(self.font_s.render("R = record", True, C_DIM),
                      (WIDTH - 80, 6))

        # CV camera preview
        self._draw_cv_preview(surf)

        # Overlay
        if self.state == self.WAITING:
            self._overlay("Flappy Blink", "Blink (or SPACE) to start")
        elif self.state == self.DEAD:
            self._overlay("DEAD",
                          f"Score  {self.score}     Best  {self.best}",
                          "Blink or SPACE to restart")

        pygame.display.flip()

    def _draw_cv_preview(self, surf):
        if self._cv_detector is None:
            return
        frame = self._cv_detector.latest_frame
        if frame is None:
            return

        import cv2 as _cv2
        # BGR → RGB, then transpose to (W, H, 3) for pygame surfarray
        rgb  = _cv2.cvtColor(frame, _cv2.COLOR_BGR2RGB)
        pg_surf = pygame.surfarray.make_surface(rgb.transpose(1, 0, 2))

        px, py = 6, 42        # top-left position in game window
        pw, ph = pg_surf.get_size()

        # Draw frame
        surf.blit(pg_surf, (px, py))

        # Red flash overlay when CV blink detected
        if self._cv_flash > 0:
            alpha   = int(180 * (self._cv_flash / 0.4))
            overlay = pygame.Surface((pw, ph), pygame.SRCALPHA)
            overlay.fill((255, 30, 30, alpha))
            surf.blit(overlay, (px, py))

        # Border — green if eyes open, orange if closed, grey if no face
        ear = self._cv_detector.ear
        if ear != ear:   # nan = no face
            border_col = (100, 100, 100)
        elif ear < 0.3:
            border_col = (255, 140, 0)
        else:
            border_col = (80, 220, 80)
        pygame.draw.rect(surf, border_col, (px - 1, py - 1, pw + 2, ph + 2), 2)

        # EAR label
        ear_str = f"EAR {ear:.1f}" if ear == ear else "no face"
        lbl     = self.font_s.render(ear_str, True, border_col)
        surf.blit(lbl, (px, py + ph + 2))

    def _overlay(self, title, line1="", line2=""):
        ov = pygame.Surface((WIDTH, GAME_H), pygame.SRCALPHA)
        ov.fill((0, 0, 0, 120))
        self.screen.blit(ov, (0, 0))
        ts = self.font_l.render(title, True, C_WHITE)
        self.screen.blit(ts, (WIDTH // 2 - ts.get_width() // 2, GAME_H // 2 - 90))
        if line1:
            s1 = self.font_m.render(line1, True, C_HUD)
            self.screen.blit(s1, (WIDTH // 2 - s1.get_width() // 2, GAME_H // 2 - 10))
        if line2:
            s2 = self.font_s.render(line2, True, C_DIM)
            self.screen.blit(s2, (WIDTH // 2 - s2.get_width() // 2, GAME_H // 2 + 28))

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self):
        while True:
            dt       = self.clock.tick(FPS) / 1000.0
            flap_key = False

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.stop_recording()
                    pygame.quit(); sys.exit()
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        self.stop_recording()
                        pygame.quit(); sys.exit()
                    if event.key in (pygame.K_SPACE, pygame.K_UP):
                        flap_key = True
                    if event.key == pygame.K_r:
                        if self._recording:
                            self.stop_recording()
                        else:
                            self.start_recording()

            self.update(dt, flap_key=flap_key)
            self.draw()


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Flappy Bird controlled by eye blinks")
    parser.add_argument("--ble",       action="store_true",
                        help="Connect to EEG Wearable over BLE")
    parser.add_argument("--cv",        action="store_true",
                        help="Enable webcam CV blink ground truth (needs mediapipe)")
    parser.add_argument("--record",    action="store_true",
                        help="Start recording immediately on launch")
    parser.add_argument("--channel",   type=int, default=0,
                        help="ADS1299 channel 0-7 (default 0=EOG; try 4=EEG central)")
    parser.add_argument("--threshold", type=float, default=150.0,
                        help="Blink threshold µV (default 150)")
    parser.add_argument("--ear",       type=float, default=0.205,
                        help="CV EAR threshold for blink (default 0.205)")
    args = parser.parse_args()

    game = FlappyBlink(channel=args.channel, threshold_uv=args.threshold)

    if args.ble:
        from python.eeg_ble        import BleEEGClient
        from python.experiments.bci.eog.eog_processing import BlinkDetector

        detector = BlinkDetector(fs=250, threshold_uv=args.threshold, debounce_s=0.5)
        _was_railed = [False]

        def on_samples(uv, gains, rails):
            ch = args.channel
            railed = bool(rails[ch])
            game._ble_channel_railed = railed
            # Still run filters so the waveform stays continuous; do not count blinks while railed.
            detector.process(uv[:, ch], trust=not railed)
            if railed and not _was_railed[0]:
                print("[BCI] ADC rail on selected channel — blink events suppressed until signal recovers")
            _was_railed[0] = railed

        def blink_source():
            return detector.pop_blink(), detector.monitor_signal, detector.last_amplitude

        client = BleEEGClient()
        client.set_sample_callback(on_samples)
        client.start()
        game.set_blink_source(blink_source)
        print(f"[BCI] BLE | CH{args.channel+1} | threshold={args.threshold} µV")
    else:
        print("[BCI] Keyboard mode — SPACE or UP to flap")

    if args.cv:
        try:
            from cv_blink import CVBlinkDetector
            cv_det = CVBlinkDetector(ear_threshold=args.ear)
            cv_det.start()
            game.set_cv_detector(cv_det)
            print(f"[CV]  Webcam ground truth | EAR threshold={args.ear}")
        except Exception as e:
            print(f"[CV]  DISABLED — {e}")
            print("[CV]  Game will run without CV ground truth (EEG blinks only)")

    if args.record:
        game.start_recording()

    game.run()
