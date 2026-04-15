"""
imu_3d_orientation.py — 3D orientation tracker over BLE (or synthetic test data)
==================================================================================
Connects to "EEG Wearable", subscribes to the IMU characteristic, computes
roll and pitch from the static gravity vector, and shows a rotating 3-D
coordinate frame in real time.  Temperature is overlaid in a corner.

Default: connects to live BLE hardware.
--test flag: simulates slow head tilts (no hardware needed)

Assumptions:
  - Device is slow-moving (static/quasi-static): gravity dominates the
    accelerometer reading, so roll/pitch can be estimated directly.
  - Yaw cannot be recovered from accelerometer alone — fixed at 0.

Requirements:
    pip install bleak matplotlib numpy

Run:
    python imu_3d_orientation.py           # live BLE (default)
    python imu_3d_orientation.py --test    # synthetic test data
"""

import asyncio
import struct
import threading
import math
import time
import argparse

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from mpl_toolkits.mplot3d import Axes3D          # noqa: F401

# ---------- Config ----------

TEST_MODE     = False
DEVICE_NAME   = "EEG Wearable"
IMU_CHAR_UUID = "12340003-1234-1234-1234-123456789abc"

# ---------- Shared state ----------

# Low-pass filter coefficient (0 = frozen, 1 = no filtering)
ALPHA = 0.15

state = {
    "x_mg":     0,
    "y_mg":     0,
    "z_mg":  1000,   # assume upright at start (1 g on Z)
    "temp":     0,
    # smoothed acceleration for roll/pitch (in mg)
    "fx": 0.0, "fy": 0.0, "fz": 1000.0,
}

def imu_callback(sender, data: bytearray):
    if len(data) < 8:
        return
    x, y, z, t = struct.unpack_from("<4h", data)
    state["x_mg"] = x
    state["y_mg"] = y
    state["z_mg"] = z
    state["temp"] = t
    # Low-pass filter to smooth out high-frequency vibration
    state["fx"] = ALPHA * x + (1 - ALPHA) * state["fx"]
    state["fy"] = ALPHA * y + (1 - ALPHA) * state["fy"]
    state["fz"] = ALPHA * z + (1 - ALPHA) * state["fz"]

# ---------- Synthetic test data ----------

def test_data_thread(stop_evt: threading.Event):
    """
    Simulates slow head tilts at 25 Hz.
    - Roll:  sinusoidal ±30° at 0.12 Hz
    - Pitch: sinusoidal ±20° at 0.08 Hz
    Gravity vector is rotated to produce synthetic x/y/z in mg.
    """
    t = 0.0
    dt = 1.0 / 25.0
    G = 1000  # mg
    while not stop_evt.is_set():
        roll  = math.radians(30) * math.sin(2 * math.pi * 0.12 * t)
        pitch = math.radians(20) * math.sin(2 * math.pi * 0.08 * t + 0.5)
        # Gravity vector in sensor frame after roll/pitch tilt
        x = int(-G * math.sin(pitch))
        y = int( G * math.sin(roll) * math.cos(pitch))
        z = int( G * math.cos(roll) * math.cos(pitch))
        tmp_cdeg = 2800 + int(30 * math.sin(2 * math.pi * 0.003 * t))
        imu_callback(None, struct.pack("<4h", x, y, z, tmp_cdeg))
        t += dt
        time.sleep(dt)

# ---------- BLE loop ----------

async def ble_loop():
    from bleak import BleakClient, BleakScanner
    print(f"Scanning for '{DEVICE_NAME}' ...")
    device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=15.0)
    if device is None:
        print(f"ERROR: '{DEVICE_NAME}' not found.")
        return
    print(f"Found {device.name} ({device.address}) — connecting ...")
    async with BleakClient(device, use_cached_services=False) as client:
        # Settle delay — Windows WinRT BLE can reject a CCCD write if it
        # arrives while MTU negotiation / connection-parameter update is still
        # in progress (WinError -2147023673).  1.0 s is enough for DLE + params.
        await asyncio.sleep(1.0)
        print("Connected. Subscribing to IMU characteristic ...")
        await client.start_notify(IMU_CHAR_UUID, imu_callback)
        print("Streaming — close the plot window to stop.")
        while True:
            await asyncio.sleep(1)

def start_ble():
    asyncio.run(ble_loop())

# ---------- Rotation helpers ----------

def make_box_edges(R, dx=0.22, dy=0.16, dz=0.015):
    """
    Returns rotated corners and edge index pairs for a thin rectangular box
    representing the PCB (wide × tall × thin).
    """
    corners = np.array([
        [-dx, -dy, -dz], [ dx, -dy, -dz], [ dx,  dy, -dz], [-dx,  dy, -dz],
        [-dx, -dy,  dz], [ dx, -dy,  dz], [ dx,  dy,  dz], [-dx,  dy,  dz],
    ])
    corners = (R @ corners.T).T   # rotate all 8 corners
    edges = [
        (0,1),(1,2),(2,3),(3,0),   # bottom face
        (4,5),(5,6),(6,7),(7,4),   # top face
        (0,4),(1,5),(2,6),(3,7),   # vertical edges
    ]
    return corners, edges

def rotation_matrix(roll_rad, pitch_rad):
    """
    Rotation matrix from roll (around X) and pitch (around Y).
    Yaw is fixed at 0 (not observable from accelerometer alone).
    """
    cr, sr = math.cos(roll_rad),  math.sin(roll_rad)
    cp, sp = math.cos(pitch_rad), math.sin(pitch_rad)
    Rx = np.array([[1,  0,   0],
                   [0,  cr, -sr],
                   [0,  sr,  cr]])
    Ry = np.array([[ cp, 0, sp],
                   [  0, 1,  0],
                   [-sp, 0, cp]])
    return Ry @ Rx

def compute_roll_pitch(fx, fy, fz):
    """Roll and pitch in radians from smoothed acceleration vector."""
    norm = math.sqrt(fx**2 + fy**2 + fz**2)
    if norm < 1e-3:
        return 0.0, 0.0
    fx, fy, fz = fx / norm, fy / norm, fz / norm
    roll  = math.atan2(fy, fz)
    pitch = math.atan2(-fx, math.sqrt(fy**2 + fz**2))
    return roll, pitch

# ---------- Matplotlib 3D setup ----------

fig = plt.figure(figsize=(8, 7), facecolor="#1a1a2e")
ax  = fig.add_subplot(111, projection="3d")
ax.set_facecolor("#16213e")

AXIS_LEN = 0.8

# Colour-coded axes: X=red, Y=green, Z=blue
colors  = ["#e94560", "#0f9b8e", "#f5a623"]
labels  = ["X", "Y", "Z"]
origins = np.zeros((3, 3))

# Initial unit vectors
init_vecs = np.eye(3) * AXIS_LEN

quivers = []
for i in range(3):
    q = ax.quiver(*origins[i], *init_vecs[i],
                  color=colors[i], linewidth=2.5, arrow_length_ratio=0.2,
                  label=labels[i])
    quivers.append(q)

box_lines = []   # PCB box wireframe — rebuilt each frame in animate()

ax.set_xlim(-1, 1)
ax.set_ylim(-1, 1)
ax.set_zlim(-1, 1)
ax.set_xlabel("X", color="#cccccc")
ax.set_ylabel("Y", color="#cccccc")
ax.set_zlabel("Z", color="#cccccc")
ax.set_title("LIS2DW12 - 3D Orientation", color="#ffffff", fontsize=12)
ax.tick_params(colors="#cccccc")
ax.legend(loc="upper left", facecolor="#1a1a2e", edgecolor="#444466",
          labelcolor="#ffffff")
ax.xaxis.pane.fill = False
ax.yaxis.pane.fill = False
ax.zaxis.pane.fill = False

# Reference frame (faint grey)
for vec in np.eye(3):
    ax.quiver(0, 0, 0, *vec, color="#444466", linewidth=0.8,
              arrow_length_ratio=0.1, alpha=0.5)

temp_text = ax.text2D(0.98, 0.97, "", transform=ax.transAxes,
                      color="#ffffff", fontsize=11, va="top", ha="right",
                      fontfamily="monospace",
                      bbox=dict(boxstyle="round,pad=0.3", facecolor="#0d0d1a",
                                edgecolor="#444466", alpha=0.8))

raw_text = ax.text2D(0.02, 0.88, "", transform=ax.transAxes,
                     color="#aaaaaa", fontsize=9, va="top",
                     fontfamily="monospace")

def animate(_frame):
    fx = state["fx"]
    fy = state["fy"]
    fz = state["fz"]

    roll, pitch = compute_roll_pitch(fx, fy, fz)
    R = rotation_matrix(roll, pitch)
    rotated = (R @ np.eye(3)).T * AXIS_LEN   # shape (3, 3): one row per axis

    # Rebuild quivers (matplotlib 3D doesn't support in-place quiver update)
    for q in quivers:
        q.remove()
    quivers.clear()
    for i in range(3):
        q = ax.quiver(0, 0, 0, *rotated[i],
                      color=colors[i], linewidth=2.5, arrow_length_ratio=0.2)
        quivers.append(q)

    # Rebuild PCB box wireframe
    for ln in box_lines:
        ln.remove()
    box_lines.clear()
    corners, edges = make_box_edges(R)
    for a, b in edges:
        ln, = ax.plot([corners[a, 0], corners[b, 0]],
                      [corners[a, 1], corners[b, 1]],
                      [corners[a, 2], corners[b, 2]],
                      color="#aaaacc", linewidth=1.0, alpha=0.75)
        box_lines.append(ln)

    t = state["temp"]
    temp_text.set_text(f"Temp: {t // 100}.{abs(t) % 100:02d} °C")
    raw_text.set_text(
        f"x={state['x_mg']:+5d} mg\n"
        f"y={state['y_mg']:+5d} mg\n"
        f"z={state['z_mg']:+5d} mg\n"
        f"roll={math.degrees(roll):+6.1f}°\n"
        f"pitch={math.degrees(pitch):+6.1f}°"
    )
    return []

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true",
                        help="Run with synthetic data (no hardware needed)")
    args = parser.parse_args()
    if args.test:
        TEST_MODE = True

    stop_evt = threading.Event()

    if TEST_MODE:
        print("[TEST] Synthetic orientation data — no hardware needed.")
        bg = threading.Thread(target=test_data_thread, args=(stop_evt,), daemon=True)
    else:
        print(f"[BLE] Connecting to '{DEVICE_NAME}' ...")
        bg = threading.Thread(target=start_ble, daemon=True)

    bg.start()

    ani = animation.FuncAnimation(fig, animate, interval=40,
                                  blit=False, cache_frame_data=False)
    plt.tight_layout()
    try:
        plt.show()
    finally:
        stop_evt.set()
