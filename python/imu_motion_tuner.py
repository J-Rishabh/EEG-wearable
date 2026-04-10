"""
imu_motion_tuner.py — Motion artifact threshold tuner for the EEG wearable
===========================================================================
Connects to "EEG Wearable" via BLE, plots the live acceleration magnitude
deviation from 1 g (i.e. |a| − 1000 mg), and flashes the background red
whenever that deviation exceeds the configurable threshold.

Use this to pick MOTION_THRESHOLD_MG before a recording session, then copy
the value into eeg_stream_pg.py.

Layout
------
  Left  — 3D orientation (PCB box), same as imu_3d_orientation.py
  Right — rolling 5-second magnitude-deviation plot with threshold line

Controls (focus the plot window first)
---------------------------------------
  + / =   raise threshold by 25 mg
  -       lower threshold by 25 mg (min 25 mg)
  h       raise holdoff by 50 ms
  g       lower holdoff by 50 ms (min 50 ms)
  q       quit

Red flash — background turns dark red while motion_active is True.
motion_active is set when |a| − 1000 mg > threshold, and stays True for
HOLDOFF_S seconds after the last spike (same logic as eeg_stream_pg.py).

Requirements
------------
    pip install bleak matplotlib numpy

Run
---
    python imu_motion_tuner.py           # live BLE (default)
    python imu_motion_tuner.py --test    # synthetic data (no hardware)
"""

import asyncio
import struct
import threading
import math
import time
import argparse
import collections

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

# ── Config ────────────────────────────────────────────────────────────────────

DEVICE_NAME   = "EEG Wearable"
IMU_CHAR_UUID = "12340003-1234-1234-1234-123456789abc"

# Starting threshold for interactive tuning — deliberately higher than the
# production default (MOTION_THRESHOLD_MG in eeg_motion.py) so you have room
# to tune downward.  Copy the final value into eeg_motion.py when done.
THRESHOLD_MG  = 200    # starting threshold (mg deviation from 1 g)
HOLDOFF_S     = 0.25   # seconds to keep flag raised after last spike
WINDOW        = 125    # 5 s × 25 Hz rolling window

from eeg_motion import ImuMotionDetector, IMU_GRAVITY_ALPHA

# ── Shared state (written by BLE/test thread, read by animate) ────────────────

_lock = threading.Lock()

_state = {
    # raw IMU
    "x_mg": 0, "y_mg": 0, "z_mg": 1000, "temp": 2500,
    # gravity estimate (low-pass of raw — for orientation display)
    "fx": 0.0, "fy": 0.0, "fz": 1000.0,
    # motion detection
    "mag_dev":     0.0,   # raw dynamic acceleration magnitude (latest)
    "dyn_smooth":  0.0,   # smoothed dynamic accel (what threshold compares against)
    "motion":      False,
    # tuning knobs (read by animate, written by key handler)
    "threshold_mg": THRESHOLD_MG,
    "holdoff_s":    HOLDOFF_S,
    "dynamic_alpha": 0.20,  # smoothing coefficient for dynamic accel output
}

# Rolling magnitude-deviation buffer (for right plot)
_devs = collections.deque([0.0] * WINDOW, maxlen=WINDOW)

# Stateful detector — gravity_alpha fixed at IMU_GRAVITY_ALPHA (from eeg_motion.py);
# threshold/holdoff/dynamic_alpha are synced from _state before each sample so the
# key handler's live adjustments take effect immediately.
_detector = ImuMotionDetector(
    threshold_mg=THRESHOLD_MG,
    holdoff_s=HOLDOFF_S,
    gravity_alpha=IMU_GRAVITY_ALPHA,
    dynamic_alpha=_state["dynamic_alpha"],
)

# ── IMU processing (called from BLE/test thread) ──────────────────────────────

def _process_sample(x, y, z, t):
    with _lock:
        # Sync any tuning knobs that the key handler may have changed
        _detector.threshold_mg  = _state["threshold_mg"]
        _detector.holdoff_s     = _state["holdoff_s"]
        _detector.dynamic_alpha = _state["dynamic_alpha"]

    dyn_raw, dyn_smooth, motion = _detector.process_sample(x, y, z)

    with _lock:
        _state["x_mg"]       = x
        _state["y_mg"]       = y
        _state["z_mg"]       = z
        _state["temp"]       = t
        # Gravity estimate lives in the detector; expose it for the 3-D orientation display
        _state["fx"] = _detector.gx
        _state["fy"] = _detector.gy
        _state["fz"] = _detector.gz
        _state["mag_dev"]    = dyn_raw
        _state["dyn_smooth"] = dyn_smooth
        _state["motion"]     = motion
        _devs.append(dyn_raw)

# ── BLE loop ──────────────────────────────────────────────────────────────────

def _imu_notify(sender, data: bytearray):
    if len(data) < 8:
        return
    x, y, z, t = struct.unpack_from("<4h", data)
    _process_sample(x, y, z, t)

async def _ble_loop():
    from bleak import BleakClient, BleakScanner
    print(f"Scanning for '{DEVICE_NAME}' ...")
    device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=15.0)
    if device is None:
        print(f"ERROR: '{DEVICE_NAME}' not found.")
        return
    print(f"Found {device.name} ({device.address}) — connecting ...")
    async with BleakClient(device, use_cached_services=False) as client:
        await asyncio.sleep(1.0)
        print("Connected. Subscribing to IMU characteristic ...")
        await client.start_notify(IMU_CHAR_UUID, _imu_notify)
        print("Streaming — close the plot window to stop.")
        while True:
            await asyncio.sleep(1)

def _start_ble():
    asyncio.run(_ble_loop())

# ── Synthetic test data ───────────────────────────────────────────────────────

def _test_thread(stop_evt: threading.Event):
    """
    Simulates slow head tilts at 25 Hz.
    Injects a strong shake event every ~6 seconds to exercise the threshold.
    """
    t = 0.0
    dt = 1.0 / 25.0
    G  = 1000
    while not stop_evt.is_set():
        roll  = math.radians(20) * math.sin(2 * math.pi * 0.12 * t)
        pitch = math.radians(15) * math.sin(2 * math.pi * 0.08 * t + 0.5)
        x = int(-G * math.sin(pitch))
        y = int( G * math.sin(roll) * math.cos(pitch))
        z = int( G * math.cos(roll) * math.cos(pitch))
        # Shake burst every ~6 s for ~0.3 s
        phase_in_cycle = t % 6.0
        if phase_in_cycle < 0.3:
            shake = int(400 * math.sin(2 * math.pi * 8.0 * t))
            x += shake
            y += shake // 2
        tmp = 2800 + int(30 * math.sin(2 * math.pi * 0.003 * t))
        _process_sample(x, y, z, tmp)
        t += dt
        time.sleep(dt)

# ── Rotation helpers (same as imu_3d_orientation.py) ─────────────────────────

def _make_box_edges(R, dx=0.22, dy=0.16, dz=0.015):
    corners = np.array([
        [-dx, -dy, -dz], [ dx, -dy, -dz], [ dx,  dy, -dz], [-dx,  dy, -dz],
        [-dx, -dy,  dz], [ dx, -dy,  dz], [ dx,  dy,  dz], [-dx,  dy,  dz],
    ])
    corners = (R @ corners.T).T
    edges = [
        (0,1),(1,2),(2,3),(3,0),
        (4,5),(5,6),(6,7),(7,4),
        (0,4),(1,5),(2,6),(3,7),
    ]
    return corners, edges

def _rotation_matrix(roll_rad, pitch_rad):
    cr, sr = math.cos(roll_rad),  math.sin(roll_rad)
    cp, sp = math.cos(pitch_rad), math.sin(pitch_rad)
    Rx = np.array([[1,  0,   0], [0,  cr, -sr], [0,  sr,  cr]])
    Ry = np.array([[ cp, 0, sp], [  0, 1,  0], [-sp, 0, cp]])
    return Ry @ Rx

def _compute_roll_pitch(fx, fy, fz):
    norm = math.sqrt(fx**2 + fy**2 + fz**2)
    if norm < 1e-3:
        return 0.0, 0.0
    fx, fy, fz = fx / norm, fy / norm, fz / norm
    roll  = math.atan2(fy, fz)
    pitch = math.atan2(-fx, math.sqrt(fy**2 + fz**2))
    return roll, pitch

# ── Figure layout ─────────────────────────────────────────────────────────────

BG_NORMAL = "#1a1a2e"
BG_MOTION = "#3a0000"

fig = plt.figure(figsize=(13, 6), facecolor=BG_NORMAL)
ax3d = fig.add_subplot(121, projection="3d")
ax3d.set_facecolor("#16213e")
axmg = fig.add_subplot(122)
axmg.set_facecolor("#16213e")
fig.subplots_adjust(left=0.04, right=0.97, top=0.88, bottom=0.10, wspace=0.28)

# 3D axes setup
AXIS_LEN = 0.8
_ax3d_colors = ["#e94560", "#0f9b8e", "#f5a623"]
_quivers    = []
_box_lines  = []
for i, (col, lbl) in enumerate(zip(_ax3d_colors, ["X","Y","Z"])):
    q = ax3d.quiver(0, 0, 0, *np.eye(3)[i] * AXIS_LEN,
                    color=col, linewidth=2.5, arrow_length_ratio=0.2, label=lbl)
    _quivers.append(q)
for vec in np.eye(3):
    ax3d.quiver(0, 0, 0, *vec, color="#444466", linewidth=0.8,
                arrow_length_ratio=0.1, alpha=0.5)
ax3d.set_xlim(-1, 1); ax3d.set_ylim(-1, 1); ax3d.set_zlim(-1, 1)
ax3d.set_xlabel("X", color="#cccccc")
ax3d.set_ylabel("Y", color="#cccccc")
ax3d.set_zlabel("Z", color="#cccccc")
ax3d.set_title("Orientation", color="#ffffff", fontsize=11)
ax3d.tick_params(colors="#cccccc")
ax3d.legend(loc="upper left", facecolor=BG_NORMAL, edgecolor="#444466",
            labelcolor="#ffffff", fontsize=8)
ax3d.xaxis.pane.fill = False
ax3d.yaxis.pane.fill = False
ax3d.zaxis.pane.fill = False

_temp_txt = ax3d.text2D(0.98, 0.97, "", transform=ax3d.transAxes,
                        color="#ffffff", fontsize=9, va="top", ha="right",
                        fontfamily="monospace",
                        bbox=dict(boxstyle="round,pad=0.2", facecolor="#0d0d1a",
                                  edgecolor="#444466", alpha=0.8))
_raw_txt = ax3d.text2D(0.02, 0.88, "", transform=ax3d.transAxes,
                       color="#aaaaaa", fontsize=8, va="top",
                       fontfamily="monospace")

# Magnitude-deviation plot
t_axis = [i / 25.0 - WINDOW / 25.0 for i in range(WINDOW)]  # −5 s … 0
_devs_smooth = collections.deque([0.0] * WINDOW, maxlen=WINDOW)  # smoothed copy for display

_line_dev, = axmg.plot(t_axis, list(_devs), color="#f5a623", linewidth=1.0,
                       alpha=0.4, label="raw dynamic accel")
_line_smooth, = axmg.plot(t_axis, list(_devs_smooth), color="#f5a623", linewidth=1.8,
                           label="smoothed (threshold compares this)")
_line_thr  = axmg.axhline(THRESHOLD_MG, color="#ef5350", linewidth=1.4,
                           linestyle="--", label="threshold")
axmg.set_xlim(t_axis[0], 0)
axmg.set_ylim(0, 600)
axmg.set_xlabel("Time (s, newest = right)", color="#cccccc")
axmg.set_ylabel("dynamic acceleration  (mg)", color="#cccccc")
axmg.set_title("Motion artifact tuner  —  dynamic accel (raw − gravity estimate)", color="#ffffff", fontsize=11)
axmg.tick_params(colors="#cccccc")
for sp in axmg.spines.values():
    sp.set_edgecolor("#444466")
axmg.legend(loc="upper left", facecolor=BG_NORMAL, edgecolor="#444466",
            labelcolor="#ffffff", fontsize=8)

_motion_txt = axmg.text(0.98, 0.95, "", transform=axmg.transAxes,
                         ha="right", va="top", fontsize=14, fontfamily="monospace",
                         color="#ef5350", fontweight="bold")
_info_txt   = axmg.text(0.02, 0.95, "", transform=axmg.transAxes,
                         ha="left", va="top", fontsize=9, fontfamily="monospace",
                         color="#cccccc",
                         bbox=dict(boxstyle="round,pad=0.3", facecolor="#0d0d1a",
                                   edgecolor="#444466", alpha=0.8))

fig.text(0.5, 0.01,
         "+/=  threshold ▲10 mg    −  threshold ▼10 mg    "
         "h  holdoff ▲50 ms    g  holdoff ▼50 ms    "
         "s  smoothing ▲    d  smoothing ▼    q  quit",
         ha="center", va="bottom", color="#666666", fontsize=8,
         fontfamily="monospace")

# ── Animation ─────────────────────────────────────────────────────────────────

def _animate(_frame):
    with _lock:
        fx         = _state["fx"]
        fy         = _state["fy"]
        fz         = _state["fz"]
        x_raw      = _state["x_mg"]
        y_raw      = _state["y_mg"]
        z_raw      = _state["z_mg"]
        temp       = _state["temp"]
        motion     = _state["motion"]
        dev        = _state["mag_dev"]
        dyn_smooth = _state["dyn_smooth"]
        thr        = _state["threshold_mg"]
        hld        = _state["holdoff_s"]
        da         = _state["dynamic_alpha"]
        devs_now   = list(_devs)
        _devs_smooth.append(dyn_smooth)
        smooth_now = list(_devs_smooth)

    # ── 3D orientation ────────────────────────────────────────────────────────
    roll, pitch = _compute_roll_pitch(fx, fy, fz)
    R = _rotation_matrix(roll, pitch)
    rotated = (R @ np.eye(3)).T * AXIS_LEN

    for q in _quivers:
        q.remove()
    _quivers.clear()
    for i in range(3):
        q = ax3d.quiver(0, 0, 0, *rotated[i],
                        color=_ax3d_colors[i], linewidth=2.5,
                        arrow_length_ratio=0.2)
        _quivers.append(q)

    for ln in _box_lines:
        ln.remove()
    _box_lines.clear()
    corners, edges = _make_box_edges(R)
    for a, b in edges:
        ln, = ax3d.plot([corners[a,0], corners[b,0]],
                        [corners[a,1], corners[b,1]],
                        [corners[a,2], corners[b,2]],
                        color="#aaaacc", linewidth=1.0, alpha=0.75)
        _box_lines.append(ln)

    t_str = f"{temp // 100}.{abs(temp) % 100:02d}"
    _temp_txt.set_text(f"Temp: {t_str} °C")
    _raw_txt.set_text(
        f"x={x_raw:+5d} mg\n"
        f"y={y_raw:+5d} mg\n"
        f"z={z_raw:+5d} mg\n"
        f"roll={math.degrees(roll):+6.1f}°\n"
        f"pitch={math.degrees(pitch):+6.1f}°"
    )

    # ── Magnitude plot ────────────────────────────────────────────────────────
    _line_dev.set_ydata(devs_now)
    _line_smooth.set_ydata(smooth_now)
    _line_thr.set_ydata([thr, thr])

    # Auto-scale Y with headroom above threshold
    hi = max(max(devs_now), thr * 1.3, 100.0)
    axmg.set_ylim(0, hi * 1.15)

    _motion_txt.set_text("MOTION" if motion else "")
    _info_txt.set_text(
        f"threshold : {thr} mg\n"
        f"holdoff   : {int(hld * 1000)} ms\n"
        f"smoothing : α={da:.2f}\n"
        f"raw accel : {dev:.0f} mg\n"
        f"smoothed  : {dyn_smooth:.0f} mg"
    )

    # ── Background flash ──────────────────────────────────────────────────────
    bg = BG_MOTION if motion else BG_NORMAL
    fig.patch.set_facecolor(bg)

    return []

# ── Key handler ───────────────────────────────────────────────────────────────

def _on_key(event):
    k = event.key
    with _lock:
        thr = _state["threshold_mg"]
        hld = _state["holdoff_s"]
        if k in ("+", "="):
            _state["threshold_mg"] = thr + 10
        elif k == "-":
            _state["threshold_mg"] = max(10, thr - 10)
        elif k == "h":
            _state["holdoff_s"] = round(hld + 0.05, 3)
        elif k == "g":
            _state["holdoff_s"] = round(max(0.05, hld - 0.05), 3)
        elif k == "s":
            _state["dynamic_alpha"] = round(min(1.0, da + 0.05), 2)
        elif k == "d":
            _state["dynamic_alpha"] = round(max(0.05, da - 0.05), 2)
    if k == "q":
        plt.close("all")

fig.canvas.mpl_connect("key_press_event", _on_key)

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true",
                        help="Synthetic data — no hardware needed")
    args = parser.parse_args()

    stop_evt = threading.Event()

    if args.test:
        print("[TEST] Synthetic IMU motion data — no hardware needed.")
        print("       A shake event fires every ~6 s.")
        bg = threading.Thread(target=_test_thread, args=(stop_evt,), daemon=True)
    else:
        print(f"[BLE] Connecting to '{DEVICE_NAME}' ...")
        bg = threading.Thread(target=_start_ble, daemon=True)

    bg.start()

    print(f"Threshold: {THRESHOLD_MG} mg  |  Holdoff: {int(HOLDOFF_S*1000)} ms")
    print("Controls: +/= raise threshold  |  - lower threshold  "
          "|  h raise holdoff  |  g lower holdoff  |  q quit")

    ani = animation.FuncAnimation(fig, _animate, interval=40,
                                  blit=False, cache_frame_data=False)
    plt.tight_layout(rect=[0, 0.04, 1, 1])
    try:
        plt.show()
    finally:
        stop_evt.set()
