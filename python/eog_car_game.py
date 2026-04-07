"""
eog_car_game.py — Eye-controlled driving game
==============================================
A pseudo-3D driving game that automatically moves forward on an infinite
landscape. Steer left/right with arrow keys now; EOG (eye-dart detection
from ADS1299 CH1) will replace the keyboard input later.

EOG integration point
---------------------
When BLE is live, call:
    game.set_eog_source(callback)
where `callback()` returns a float in [-1.0, 1.0]:
    -1.0 = hard left saccade
     0.0 = centre / no movement
    +1.0 = hard right saccade

Everything else in the game stays the same — `get_steering()` calls
the registered source transparently.

Requirements:
    pip install pygame

Run:
    python eog_car_game.py
"""

import pygame
import sys
import math
import collections
import numpy as np

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WIDTH, HEIGHT = 960, 640
FPS           = 60

HORIZON_Y     = HEIGHT // 2 - 20    # where sky meets road
ROAD_W_NEAR   = 520                  # road width at screen bottom
ROAD_W_FAR    = 72                   # road width at horizon

FORWARD_SPEED = 260.0               # pixels / sec (lane-marker scroll rate)
STEER_SPEED   = 240.0               # pixels / sec lateral
STEER_RETURN  = 3.5                 # how fast car re-centres when key released

# Road bounds (car cannot leave road)
ROAD_LEFT  = WIDTH // 2 - ROAD_W_NEAR // 2 + 36
ROAD_RIGHT = WIDTH // 2 + ROAD_W_NEAR // 2 - 36

# Palette
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

# ---------------------------------------------------------------------------
# Perspective helpers
# ---------------------------------------------------------------------------

def persp_t(t: float) -> float:
    """Map linear t∈[0,1] (0=horizon, 1=bottom) to perspective-squished t."""
    return t * t

def road_x_at(t: float) -> tuple[int, int]:
    """Left and right road edge x-coords at depth parameter t (perspective)."""
    tp   = persp_t(t)
    half = (ROAD_W_FAR + (ROAD_W_NEAR - ROAD_W_FAR) * tp) / 2
    cx   = WIDTH // 2
    return int(cx - half), int(cx + half)

def screen_y_at(t: float) -> int:
    """Screen y at depth parameter t (t=0 → horizon, t=1 → bottom)."""
    return int(HORIZON_Y + t * (HEIGHT - HORIZON_Y))

# ---------------------------------------------------------------------------
# Side objects — fence posts / trees for depth cues
# ---------------------------------------------------------------------------

class SideMarker:
    """A fence post drawn on both sides of the road, scrolling towards viewer."""

    def __init__(self, t: float):
        self.t = t       # depth in [0, 1]
        self.kind = "post"

    def update(self, dt: float, speed: float):
        # Convert forward speed to t-space advance (further = slower visual)
        dt_t = dt * speed / (HEIGHT - HORIZON_Y) * (1.0 - self.t + 0.1)
        self.t += dt_t

    def draw(self, surface: pygame.Surface):
        tp  = persp_t(self.t)
        y   = screen_y_at(self.t)
        lx, rx = road_x_at(self.t)

        # Scale with depth
        h = max(4, int(55 * tp))
        w = max(2, int(7  * tp))

        offset = max(4, int(22 * tp))  # gap between post and road edge

        for x in (lx - offset - w, rx + offset):
            # Post
            color = (120, 80, 40)
            pygame.draw.rect(surface, color, (x, y - h, w, h))
            # Cap
            pygame.draw.rect(surface, (160, 110, 60), (x - 1, y - h - 2, w + 2, 3))

# ---------------------------------------------------------------------------
# Main game class
# ---------------------------------------------------------------------------

class EogCarGame:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption("EOG Car — Arrow keys to steer")
        self.clock  = pygame.time.Clock()
        self.font_s = pygame.font.SysFont("monospace", 16)
        self.font_m = pygame.font.SysFont("monospace", 20, bold=True)

        # Car state
        self.car_x    = float(WIDTH // 2)   # centre x of car
        self.car_vel  = 0.0                  # lateral velocity (pixels/s)

        # Road scroll (gives illusion of forward motion)
        self.scroll_t = 0.0

        # Side markers
        self._init_markers()

        # Steering history for EOG smoothing (displayed in HUD)
        self._steer_hist: collections.deque[float] = collections.deque(
            [0.0] * 20, maxlen=20)

        # LiDAR dome rotation angle (degrees, advances each frame)
        self.lidar_angle = 0.0

        # --- EOG integration point ---
        # Replace None with a callable () → float when BLE is live.
        # The callable should return a signal in [-1.0, 1.0] where
        # negative=left, positive=right, magnitude encodes strength.
        self._eog_source = None

    # ------------------------------------------------------------------
    # EOG / keyboard control interface
    # ------------------------------------------------------------------

    def set_eog_source(self, callback):
        """
        Register a live EOG signal source.

        callback() must return float in [-1.0, 1.0]:
          - Negative → leftward eye saccade
          - Positive → rightward eye saccade
          -  0.0     → eyes centred / no saccade detected

        Example (to wire up later):
            shared_eog = [0.0]   # updated by BLE thread

            def eog_callback():
                raw = shared_eog[0]
                if   raw >  EOG_THRESHOLD: return  1.0
                elif raw < -EOG_THRESHOLD: return -1.0
                else:                      return  0.0

            game.set_eog_source(eog_callback)
        """
        self._eog_source = callback

    def get_steering(self) -> float:
        """
        Returns steering signal in [-1.0, 1.0].

        Source priority:
          1. EOG callback (if registered via set_eog_source)
          2. Keyboard arrow keys (fallback / development mode)

        This is the only function that needs to change when wiring
        up real eye-tracking data from the EEG wearable.
        """
        if self._eog_source is not None:
            return float(self._eog_source())

        # Keyboard fallback
        keys = pygame.key.get_pressed()
        if keys[pygame.K_LEFT]:
            return -1.0
        if keys[pygame.K_RIGHT]:
            return 1.0
        return 0.0

    # ------------------------------------------------------------------
    # Game logic
    # ------------------------------------------------------------------

    def _init_markers(self):
        """Seed fence posts spread across depth range."""
        self.markers: list[SideMarker] = []
        N = 12
        for i in range(N):
            m = SideMarker(t=(i + 0.5) / N)
            self.markers.append(m)

    def update(self, dt: float):
        steering = self.get_steering()
        self._steer_hist.append(steering)

        # Lateral dynamics — smooth acceleration/deceleration
        target_vel = steering * STEER_SPEED
        blend = 12.0 if abs(steering) > 0.05 else STEER_RETURN
        self.car_vel += (target_vel - self.car_vel) * min(1.0, blend * dt)
        self.car_x   += self.car_vel * dt
        self.car_x    = max(float(ROAD_LEFT), min(float(ROAD_RIGHT), self.car_x))

        # Road scroll (t advances at a rate proportional to speed)
        scroll_rate = FORWARD_SPEED / (HEIGHT - HORIZON_Y)
        self.scroll_t = (self.scroll_t + scroll_rate * dt) % 1.0

        # LiDAR dome — 1.5 full rotations per second
        self.lidar_angle = (self.lidar_angle + 540.0 * dt) % 360.0

        # Side markers
        for m in self.markers:
            m.update(dt, FORWARD_SPEED)
        # Recycle markers that have passed the camera
        for m in self.markers:
            if m.t >= 1.0:
                m.t = 0.01

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw_sky(self):
        """Vertical gradient sky."""
        for y in range(HORIZON_Y):
            t   = y / max(1, HORIZON_Y)
            col = tuple(int(C_SKY_TOP[i] + (C_SKY_BOT[i] - C_SKY_TOP[i]) * t)
                        for i in range(3))
            pygame.draw.line(self.screen, col, (0, y), (WIDTH, y))

    def _draw_ground(self):
        """Alternating grass stripes for depth illusion."""
        N_STRIPES = 14
        for i in range(N_STRIPES):
            t0 = i / N_STRIPES
            t1 = (i + 1) / N_STRIPES
            y0 = screen_y_at(t0)
            y1 = screen_y_at(t1)
            col = C_GROUND if i % 2 == 0 else C_GROUND2
            pygame.draw.rect(self.screen, col, (0, y0, WIDTH, y1 - y0))

    def _draw_road(self):
        """Perspective road with edge lines and scrolling centre dashes."""
        lnear, rnear = road_x_at(1.0)
        lfar,  rfar  = road_x_at(0.0)

        road_poly = [
            (lfar,  HORIZON_Y),
            (rfar,  HORIZON_Y),
            (rnear, HEIGHT),
            (lnear, HEIGHT),
        ]
        pygame.draw.polygon(self.screen, C_ROAD, road_poly)

        # --- Edge lines ---
        N_EDGE = 24
        prev_l, prev_r, prev_y = None, None, None
        for i in range(N_EDGE + 1):
            t  = i / N_EDGE
            y  = screen_y_at(t)
            lx, rx = road_x_at(t)
            w = max(1, int(3 * persp_t(t)))
            if prev_y is not None:
                pygame.draw.line(self.screen, C_ROAD_EDGE,
                                 (prev_l, prev_y), (lx, y), w)
                pygame.draw.line(self.screen, C_ROAD_EDGE,
                                 (prev_r, prev_y), (rx, y), w)
            prev_l, prev_r, prev_y = lx, rx, y

        # --- Centre dashes (scroll forward) ---
        N_DASH = 16
        for i in range(N_DASH):
            # raw t for this dash slot
            t_raw = (i / N_DASH + self.scroll_t) % 1.0
            # only draw the "on" half of the dash cycle
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
        # Draw back-to-front (painter's algorithm)
        for m in sorted(self.markers, key=lambda m: m.t):
            m.draw(self.screen)

    def _draw_car(self):
        """
        Waymo Jaguar I-PACE — white autonomous vehicle with:
          - Sleek white body + dark lower bumper
          - Waymo teal/blue accent stripe
          - Roof-mounted LiDAR dome with animated sweep beam
          - Side sensor pods (short-range ultrasonic/camera)
          - LED tail lights (Waymo red bar style)
          - Waymo "W" badge
        """
        x = int(self.car_x)
        y = HEIGHT - 78

        # ---- Dimensions ----
        BW, BH = 86, 42    # body width, height
        RW, RH = 62, 22    # roof width, height
        WW, WH = 15, 11    # wheel width, height

        surf = self.screen

        # ---- Ground shadow ----
        shadow_surf = pygame.Surface((BW - 8, 10), pygame.SRCALPHA)
        shadow_surf.fill((0, 0, 0, 0))
        pygame.draw.ellipse(shadow_surf, (0, 0, 0, 60), (0, 0, BW - 8, 10))
        surf.blit(shadow_surf, (x - (BW - 8)//2, y + BH//2))

        # ---- Wheels ----
        wheel_positions = [
            (x - BW//2 - 3, y - BH//4 - WH//2),   # front-left
            (x + BW//2 - WW + 3, y - BH//4 - WH//2),  # front-right
            (x - BW//2 - 3, y + BH//4 - WH//2),   # rear-left
            (x + BW//2 - WW + 3, y + BH//4 - WH//2),  # rear-right
        ]
        for wx, wy in wheel_positions:
            # Tyre
            pygame.draw.rect(surf, (28, 28, 28),
                             pygame.Rect(wx, wy, WW, WH), border_radius=4)
            # Alloy rim (Waymo uses silver split-spoke rims)
            cx, cy = wx + WW//2, wy + WH//2
            pygame.draw.circle(surf, (160, 165, 170), (cx, cy), WH//2 - 1)
            pygame.draw.circle(surf, (80, 82, 85),    (cx, cy), WH//2 - 3)
            # Spokes
            for ang in range(0, 360, 72):
                rad = math.radians(ang)
                ex = cx + int((WH//2 - 2) * math.cos(rad))
                ey = cy + int((WH//2 - 2) * math.sin(rad))
                pygame.draw.line(surf, (150, 155, 158), (cx, cy), (ex, ey), 1)
            pygame.draw.circle(surf, (50, 52, 55), (cx, cy), 2)

        # ---- Lower body / bumper (dark charcoal) ----
        lower = pygame.Rect(x - BW//2, y + BH//4, BW, BH//4 + 2)
        pygame.draw.rect(surf, (38, 40, 44), lower, border_radius=4)

        # ---- Main body (Waymo white) ----
        body = pygame.Rect(x - BW//2, y - BH//2, BW, BH)
        pygame.draw.rect(surf, (242, 243, 244), body, border_radius=8)

        # ---- Waymo teal accent stripe ----
        stripe_y = y + BH//2 - 10
        stripe = pygame.Rect(x - BW//2 + 6, stripe_y, BW - 12, 5)
        pygame.draw.rect(surf, (0, 178, 169), stripe, border_radius=2)

        # ---- Roof ----
        rx = x - RW//2
        ry = y - BH//2 - RH + 2
        roof = pygame.Rect(rx, ry, RW, RH)
        pygame.draw.rect(surf, (230, 232, 233), roof, border_radius=6)

        # ---- Rear windscreen ----
        ws = pygame.Rect(x - RW//2 + 4, ry + 3, RW - 8, RH - 6)
        pygame.draw.rect(surf, (60, 80, 105), ws, border_radius=3)
        # Glare highlight
        pygame.draw.line(surf, (120, 160, 200),
                         (ws.left + 4, ws.top + 2),
                         (ws.left + 12, ws.top + 2), 1)

        # ---- Waymo sensor bar (runs across top of windscreen) ----
        sbar = pygame.Rect(x - RW//2 + 2, ry + 1, RW - 4, 3)
        pygame.draw.rect(surf, (10, 10, 12), sbar)
        # Tiny camera dots on sensor bar
        for cx_off in (-16, -6, 4, 14):
            cam_x = x + cx_off
            pygame.draw.circle(surf, (0, 178, 169), (cam_x, ry + 2), 2)

        # ---- Side sensor pods (short-range cameras/ultrasonic) ----
        for sx, sy in [(x - BW//2 - 1, y - 8), (x + BW//2 - 5, y - 8)]:
            pod = pygame.Rect(sx, sy, 6, 10)
            pygame.draw.rect(surf, (30, 32, 36), pod, border_radius=2)
            pygame.draw.circle(surf, (0, 140, 134), (sx + 3, sy + 3), 2)

        # ---- Sensor box (replaces LiDAR dome) ----
        box_w, box_h = 22, 14
        box_x = x - box_w // 2
        box_y = ry - box_h
        pygame.draw.rect(surf, (38, 40, 44),
                         pygame.Rect(box_x, box_y, box_w, box_h), border_radius=2)
        pygame.draw.rect(surf, (60, 64, 68),
                         pygame.Rect(box_x, box_y, box_w, box_h), 1, border_radius=2)

        # ---- LED tail lights (Waymo full-width red bar) ----
        tl_y = y - BH//2 + 2
        # Full-width thin red bar
        pygame.draw.rect(surf, (180, 20, 20),
                         pygame.Rect(x - BW//2 + 4, tl_y, BW - 8, 3))
        # Bright LED clusters at each end
        for tlx in (x - BW//2 + 4, x + BW//2 - 16):
            pygame.draw.rect(surf, (255, 40, 40),
                             pygame.Rect(tlx, tl_y - 1, 12, 5), border_radius=1)

        # ---- Waymo "W" badge (rear centre) ----
        badge_x, badge_y = x - 7, y - BH//2 + 8
        font_badge = pygame.font.SysFont("arial", 11, bold=True)
        w_surf = font_badge.render("W", True, (0, 178, 169))
        surf.blit(w_surf, (badge_x, badge_y))

    def _draw_hud(self, steering: float):
        mode = "EOG" if self._eog_source else "KEYBOARD"
        col  = (80, 220, 80) if self._eog_source else C_HUD_DIM

        # Mode badge
        badge = self.font_s.render(f"[ {mode} ]", True, col)
        self.screen.blit(badge, (12, 12))

        # Steering bar
        BAR_W = 180
        BAR_H = 12
        bx, by = 12, 36
        pygame.draw.rect(self.screen, (40, 40, 50),
                         (bx, by, BAR_W, BAR_H), border_radius=3)
        fill_x = int(BAR_W // 2 + steering * BAR_W // 2)
        mid    = bx + BAR_W // 2
        left   = min(mid, bx + fill_x)
        right  = max(mid, bx + fill_x)
        if right > left:
            bar_col = (80, 180, 255) if steering < 0 else (255, 120, 60)
            pygame.draw.rect(self.screen, bar_col,
                             (left, by, right - left, BAR_H), border_radius=2)
        pygame.draw.rect(self.screen, C_HUD_DIM,
                         (bx, by, BAR_W, BAR_H), 1, border_radius=3)

        steer_lbl = self.font_s.render(
            f"{'LEFT' if steering < -0.05 else ('RIGHT' if steering > 0.05 else 'STRAIGHT'):>8}",
            True, C_HUD_FG)
        self.screen.blit(steer_lbl, (bx + BAR_W + 8, by - 2))

        # ESC hint
        esc = self.font_s.render("ESC to quit", True, (80, 80, 90))
        self.screen.blit(esc, (WIDTH - 110, HEIGHT - 24))

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self):
        while True:
            dt = self.clock.tick(FPS) / 1000.0

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        pygame.quit()
                        sys.exit()

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
    parser.add_argument("--ble", action="store_true",
                        help="Connect to EEG Wearable over BLE for live EOG steering")
    parser.add_argument("--passthrough", action="store_true",
                        help="Use continuous EOG amplitude instead of saccade detection")
    parser.add_argument("--threshold", type=float, default=50.0,
                        help="Saccade detection threshold in µV (default: 50)")
    args = parser.parse_args()

    game = EogCarGame()

    if args.ble:
        from eog_ble        import BleEEGClient
        from eog_processing import EOGSaccadeDetector, EOGPassthrough
        import numpy as np

        # Choose processing mode
        if args.passthrough:
            detector = EOGPassthrough(fs=250, scale_uv=args.threshold)
            print(f"[EOG] Passthrough mode — scale={args.threshold} µV → ±1.0")
        else:
            detector = EOGSaccadeDetector(fs=250, threshold_uv=args.threshold)
            print(f"[EOG] Saccade mode — threshold={args.threshold} µV")

        # BLE sample callback — runs on BLE thread, feeds detector
        def on_samples(samples: "np.ndarray", gain: int):
            """samples shape (N, 8) in µV; CH1 = EOG electrode."""
            eog = samples[:, 0]          # CH1
            detector.process(eog)        # updates detector.steering

        client = BleEEGClient()
        client.set_sample_callback(on_samples)
        client.start()

        # Register EOG source with game — called each frame on main thread
        game.set_eog_source(lambda: detector.steering)

    game.run()
