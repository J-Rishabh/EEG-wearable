"""
eog_car_game.py — Eye-controlled driving game
==============================================
A pseudo-3D driving game steered by EOG (horizontal eye saccades).
Steer left/right with arrow keys (fallback) or EOG from ADS1299 CH1.

Run
---
    python eog_car_game.py               # keyboard
    python eog_car_game.py --ble         # live EOG via BLE
    python eog_car_game.py --ble --passthrough --threshold 80
"""

import pygame
import sys
import os
import math
import collections
import numpy as np

# ── path setup ────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..", "..", "..", "..")   # → python/
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)

# ── config ────────────────────────────────────────────────────────────────────

WIDTH, HEIGHT = 960, 640
FPS           = 60

HORIZON_Y     = HEIGHT // 2 - 20
ROAD_W_NEAR   = 520
ROAD_W_FAR    = 72

FORWARD_SPEED = 260.0
STEER_SPEED   = 240.0
STEER_RETURN  = 3.5

ROAD_LEFT  = WIDTH // 2 - ROAD_W_NEAR // 2 + 36
ROAD_RIGHT = WIDTH // 2 + ROAD_W_NEAR // 2 - 36

C_SKY_TOP   = (18,  55, 140)
C_SKY_BOT   = (88, 148, 220)
C_GROUND    = (28, 105, 28)
C_GROUND2   = (22,  88, 22)
C_ROAD      = (55,  58,  68)
C_ROAD_EDGE = (200, 200, 200)
C_DASH      = (240, 220,  50)
C_CAR_BODY  = (210,  40,  40)
C_CAR_ROOF  = (170,  25,  25)
C_GLASS     = (160, 210, 245)
C_WHEEL     = ( 22,  22,  22)
C_WHITE     = (255, 255, 255)
C_HUD_FG    = (240, 240, 240)
C_HUD_DIM   = (130, 130, 130)


# ── perspective helpers ───────────────────────────────────────────────────────

def persp_t(t):
    return t * t

def road_x_at(t):
    tp   = persp_t(t)
    half = (ROAD_W_FAR + (ROAD_W_NEAR - ROAD_W_FAR) * tp) / 2
    cx   = WIDTH // 2
    return int(cx - half), int(cx + half)

def screen_y_at(t):
    return int(HORIZON_Y + t * (HEIGHT - HORIZON_Y))


# ── side markers ─────────────────────────────────────────────────────────────

class SideMarker:
    def __init__(self, t):
        self.t = t

    def update(self, dt, speed):
        dt_t = dt * speed / (HEIGHT - HORIZON_Y) * (1.0 - self.t + 0.1)
        self.t += dt_t

    def draw(self, surface):
        tp  = persp_t(self.t)
        y   = screen_y_at(self.t)
        lx, rx = road_x_at(self.t)
        h = max(4, int(55 * tp))
        w = max(2, int(7  * tp))
        offset = max(4, int(22 * tp))
        for x in (lx - offset - w, rx + offset):
            pygame.draw.rect(surface, (120, 80, 40), (x, y - h, w, h))
            pygame.draw.rect(surface, (160, 110, 60), (x - 1, y - h - 2, w + 2, 3))


# ── main game ─────────────────────────────────────────────────────────────────

class EogCarGame:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption("EOG Car — Arrow keys to steer")
        self.clock  = pygame.time.Clock()
        self.font_s = pygame.font.SysFont("monospace", 16)
        self.font_m = pygame.font.SysFont("monospace", 20, bold=True)

        self.car_x    = float(WIDTH // 2)
        self.car_vel  = 0.0
        self.scroll_t = 0.0
        self._init_markers()
        self._steer_hist = collections.deque([0.0] * 20, maxlen=20)
        self.lidar_angle = 0.0
        self._eog_source = None

    def set_eog_source(self, callback):
        """
        callback() → float in [-1.0, 1.0]
          negative = left saccade, positive = right saccade
        """
        self._eog_source = callback

    def get_steering(self):
        if self._eog_source is not None:
            return float(self._eog_source())
        keys = pygame.key.get_pressed()
        if keys[pygame.K_LEFT]:  return -1.0
        if keys[pygame.K_RIGHT]: return  1.0
        return 0.0

    def _init_markers(self):
        self.markers = [SideMarker((i + 0.5) / 12) for i in range(12)]

    def update(self, dt):
        steering = self.get_steering()
        self._steer_hist.append(steering)
        target_vel = steering * STEER_SPEED
        blend = 12.0 if abs(steering) > 0.05 else STEER_RETURN
        self.car_vel += (target_vel - self.car_vel) * min(1.0, blend * dt)
        self.car_x   += self.car_vel * dt
        self.car_x    = max(float(ROAD_LEFT), min(float(ROAD_RIGHT), self.car_x))
        scroll_rate   = FORWARD_SPEED / (HEIGHT - HORIZON_Y)
        self.scroll_t = (self.scroll_t + scroll_rate * dt) % 1.0
        self.lidar_angle = (self.lidar_angle + 540.0 * dt) % 360.0
        for m in self.markers:
            m.update(dt, FORWARD_SPEED)
        for m in self.markers:
            if m.t >= 1.0:
                m.t = 0.01

    def _draw_sky(self):
        for y in range(HORIZON_Y):
            t   = y / max(1, HORIZON_Y)
            col = tuple(int(C_SKY_TOP[i] + (C_SKY_BOT[i] - C_SKY_TOP[i]) * t)
                        for i in range(3))
            pygame.draw.line(self.screen, col, (0, y), (WIDTH, y))

    def _draw_ground(self):
        N_STRIPES = 14
        for i in range(N_STRIPES):
            t0 = i / N_STRIPES
            t1 = (i + 1) / N_STRIPES
            y0 = screen_y_at(t0)
            y1 = screen_y_at(t1)
            col = C_GROUND if i % 2 == 0 else C_GROUND2
            pygame.draw.rect(self.screen, col, (0, y0, WIDTH, y1 - y0))

    def _draw_road(self):
        lnear, rnear = road_x_at(1.0)
        lfar,  rfar  = road_x_at(0.0)
        road_poly = [(lfar, HORIZON_Y), (rfar, HORIZON_Y),
                     (rnear, HEIGHT),   (lnear, HEIGHT)]
        pygame.draw.polygon(self.screen, C_ROAD, road_poly)

        N_EDGE = 24
        prev_l = prev_r = prev_y = None
        for i in range(N_EDGE + 1):
            t  = i / N_EDGE
            y  = screen_y_at(t)
            lx, rx = road_x_at(t)
            w  = max(1, int(3 * persp_t(t)))
            if prev_y is not None:
                pygame.draw.line(self.screen, C_ROAD_EDGE,
                                 (prev_l, prev_y), (lx, y), w)
                pygame.draw.line(self.screen, C_ROAD_EDGE,
                                 (prev_r, prev_y), (rx, y), w)
            prev_l, prev_r, prev_y = lx, rx, y

        N_DASH = 16
        for i in range(N_DASH):
            t_raw = (i / N_DASH + self.scroll_t) % 1.0
            if (t_raw * N_DASH) % 1.0 > 0.55:
                continue
            t  = t_raw
            tp = persp_t(t)
            y  = screen_y_at(t)
            h  = max(2, int(18 * tp))
            w  = max(1, int(5  * tp))
            pygame.draw.rect(self.screen, C_DASH,
                             (WIDTH // 2 - w // 2, y - h // 2, w, h))

    def _draw_markers(self):
        for m in sorted(self.markers, key=lambda m: m.t):
            m.draw(self.screen)

    def _draw_car(self):
        x = int(self.car_x)
        y = HEIGHT - 78

        BW, BH = 86, 42
        RW, RH = 62, 22
        WW, WH = 15, 11

        surf = self.screen

        shadow_surf = pygame.Surface((BW - 8, 10), pygame.SRCALPHA)
        shadow_surf.fill((0, 0, 0, 0))
        pygame.draw.ellipse(shadow_surf, (0, 0, 0, 60), (0, 0, BW - 8, 10))
        surf.blit(shadow_surf, (x - (BW - 8)//2, y + BH//2))

        wheel_positions = [
            (x - BW//2 - 3, y - BH//4 - WH//2),
            (x + BW//2 - WW + 3, y - BH//4 - WH//2),
            (x - BW//2 - 3, y + BH//4 - WH//2),
            (x + BW//2 - WW + 3, y + BH//4 - WH//2),
        ]
        for wx, wy in wheel_positions:
            pygame.draw.rect(surf, (28, 28, 28), pygame.Rect(wx, wy, WW, WH), border_radius=4)
            cx, cy = wx + WW//2, wy + WH//2
            pygame.draw.circle(surf, (160, 165, 170), (cx, cy), WH//2 - 1)
            pygame.draw.circle(surf, (80, 82, 85),    (cx, cy), WH//2 - 3)
            for ang in range(0, 360, 72):
                rad = math.radians(ang)
                ex = cx + int((WH//2 - 2) * math.cos(rad))
                ey = cy + int((WH//2 - 2) * math.sin(rad))
                pygame.draw.line(surf, (150, 155, 158), (cx, cy), (ex, ey), 1)
            pygame.draw.circle(surf, (50, 52, 55), (cx, cy), 2)

        pygame.draw.rect(surf, (38, 40, 44),
                         pygame.Rect(x - BW//2, y + BH//4, BW, BH//4 + 2), border_radius=4)
        pygame.draw.rect(surf, (242, 243, 244),
                         pygame.Rect(x - BW//2, y - BH//2, BW, BH), border_radius=8)
        stripe_y = y + BH//2 - 10
        pygame.draw.rect(surf, (0, 178, 169),
                         pygame.Rect(x - BW//2 + 6, stripe_y, BW - 12, 5), border_radius=2)

        rx = x - RW//2
        ry = y - BH//2 - RH + 2
        pygame.draw.rect(surf, (230, 232, 233),
                         pygame.Rect(rx, ry, RW, RH), border_radius=6)

        ws = pygame.Rect(x - RW//2 + 4, ry + 3, RW - 8, RH - 6)
        pygame.draw.rect(surf, (60, 80, 105), ws, border_radius=3)
        pygame.draw.line(surf, (120, 160, 200),
                         (ws.left + 4, ws.top + 2), (ws.left + 12, ws.top + 2), 1)

        sbar = pygame.Rect(x - RW//2 + 2, ry + 1, RW - 4, 3)
        pygame.draw.rect(surf, (10, 10, 12), sbar)
        for cx_off in (-16, -6, 4, 14):
            cam_x = x + cx_off
            pygame.draw.circle(surf, (0, 178, 169), (cam_x, ry + 2), 2)

        for sx, sy in [(x - BW//2 - 1, y - 8), (x + BW//2 - 5, y - 8)]:
            pod = pygame.Rect(sx, sy, 6, 10)
            pygame.draw.rect(surf, (30, 32, 36), pod, border_radius=2)
            pygame.draw.circle(surf, (0, 140, 134), (sx + 3, sy + 3), 2)

        box_w, box_h = 22, 14
        box_x = x - box_w // 2
        box_y = ry - box_h
        pygame.draw.rect(surf, (38, 40, 44),
                         pygame.Rect(box_x, box_y, box_w, box_h), border_radius=2)

        tl_y = y - BH//2 + 2
        pygame.draw.rect(surf, (180, 20, 20),
                         pygame.Rect(x - BW//2 + 4, tl_y, BW - 8, 3))
        for tlx in (x - BW//2 + 4, x + BW//2 - 16):
            pygame.draw.rect(surf, (255, 40, 40),
                             pygame.Rect(tlx, tl_y - 1, 12, 5), border_radius=1)

        badge_x, badge_y = x - 7, y - BH//2 + 8
        font_badge = pygame.font.SysFont("arial", 11, bold=True)
        surf.blit(font_badge.render("W", True, (0, 178, 169)), (badge_x, badge_y))

    def _draw_hud(self, steering):
        mode = "EOG" if self._eog_source else "KEYBOARD"
        col  = (80, 220, 80) if self._eog_source else C_HUD_DIM
        self.screen.blit(self.font_s.render(f"[ {mode} ]", True, col), (12, 12))

        BAR_W, BAR_H = 180, 12
        bx, by = 12, 36
        pygame.draw.rect(self.screen, (40, 40, 50), (bx, by, BAR_W, BAR_H), border_radius=3)
        fill_x = int(BAR_W // 2 + steering * BAR_W // 2)
        mid    = bx + BAR_W // 2
        left   = min(mid, bx + fill_x)
        right  = max(mid, bx + fill_x)
        if right > left:
            bar_col = (80, 180, 255) if steering < 0 else (255, 120, 60)
            pygame.draw.rect(self.screen, bar_col,
                             (left, by, right - left, BAR_H), border_radius=2)
        pygame.draw.rect(self.screen, C_HUD_DIM, (bx, by, BAR_W, BAR_H), 1, border_radius=3)
        steer_lbl = self.font_s.render(
            f"{'LEFT' if steering < -0.05 else ('RIGHT' if steering > 0.05 else 'STRAIGHT'):>8}",
            True, C_HUD_FG)
        self.screen.blit(steer_lbl, (bx + BAR_W + 8, by - 2))
        self.screen.blit(self.font_s.render("ESC to quit", True, (80, 80, 90)),
                         (WIDTH - 110, HEIGHT - 24))

    def run(self):
        while True:
            dt = self.clock.tick(FPS) / 1000.0
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit(); sys.exit()
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        pygame.quit(); sys.exit()

            steering = self.get_steering()
            self.update(dt)
            self._draw_sky()
            self._draw_ground()
            self._draw_road()
            self._draw_markers()
            self._draw_car()
            self._draw_hud(steering)
            pygame.display.flip()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ble",         action="store_true")
    parser.add_argument("--passthrough", action="store_true")
    parser.add_argument("--threshold",   type=float, default=50.0)
    args = parser.parse_args()

    game = EogCarGame()

    if args.ble:
        from eeg_ble        import BleEEGClient
        from python.experiments.bci.eog.eog_processing import EOGSaccadeDetector, EOGPassthrough

        if args.passthrough:
            detector = EOGPassthrough(fs=250, scale_uv=args.threshold)
            print(f"[EOG] Passthrough mode — scale={args.threshold} µV → ±1.0")
        else:
            detector = EOGSaccadeDetector(fs=250, threshold_uv=args.threshold)
            print(f"[EOG] Saccade mode — threshold={args.threshold} µV")

        def on_samples(uv, gains, _rails):
            """uv shape (8, 8) µV [sample, channel]; CH1 (idx 0) = EOG."""
            detector.process(uv[:, 0])

        client = BleEEGClient()
        client.set_sample_callback(on_samples)
        client.start()
        game.set_eog_source(lambda: detector.steering)

    game.run()
