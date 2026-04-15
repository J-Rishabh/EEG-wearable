"""
imu_live_graph.py — Live 3-axis IMU graph over BLE (or synthetic test data)
=============================================================================
Connects to the "EEG Wearable" nRF54L15 via BLE, subscribes to the IMU
characteristic (UUID 12340003-...), and shows a rolling 5-second plot of
X / Y / Z acceleration in mg, plus a live temperature readout.

TEST_MODE = True  → generates synthetic IMU motion (no hardware needed)
TEST_MODE = False → connects to real BLE device

Requirements:
    pip install bleak matplotlib

Run:
    python imu_live_graph.py           # live BLE (default)
    python imu_live_graph.py --test    # synthetic test data
"""

import asyncio
import struct
import threading
import collections
import time
import math
import argparse

import matplotlib.pyplot as plt
import matplotlib.animation as animation

# ---------- Config ----------

TEST_MODE   = False
DEVICE_NAME = "EEG Wearable"
IMU_CHAR_UUID = "12340003-1234-1234-1234-123456789abc"

# ---------- Shared data (thread-safe via deque) ----------

WINDOW = 125          # 5 s × 25 Hz
xs = collections.deque([0] * WINDOW, maxlen=WINDOW)
ys = collections.deque([0] * WINDOW, maxlen=WINDOW)
zs = collections.deque([0] * WINDOW, maxlen=WINDOW)
temp_cdeg = [2500]    # start at 25.00 °C

def imu_callback(sender, data: bytearray):
    """Called from the bleak asyncio thread on every BLE notification."""
    if len(data) < 8:
        return
    x, y, z, t = struct.unpack_from("<4h", data)
    xs.append(x)
    ys.append(y)
    zs.append(z)
    temp_cdeg[0] = t

# ---------- Synthetic test data generator ----------

def test_data_thread(stop_evt: threading.Event):
    """
    Simulates LIS2DW12 output at 25 Hz.
    - Slow tilt (head turning): 0.2 Hz sinusoid on X/Y
    - Z: gravity baseline ~1000 mg + gentle oscillation
    - Temperature: slow drift around 28 °C
    """
    t = 0.0
    dt = 1.0 / 25.0
    while not stop_evt.is_set():
        # Simulate slow head motion
        x = int(200 * math.sin(2 * math.pi * 0.2 * t))
        y = int(150 * math.sin(2 * math.pi * 0.15 * t + 1.0))
        z = int(980 + 40 * math.sin(2 * math.pi * 0.08 * t))   # near 1 g
        # Occasional head shake (every ~6 s)
        if int(t * 25) % 150 < 5:
            x += int(300 * math.sin(2 * math.pi * 3.0 * t))
        xs.append(x)
        ys.append(y)
        zs.append(z)
        # Temperature: slow drift 28.00 → 28.50 °C over time
        temp_cdeg[0] = 2800 + int(50 * math.sin(2 * math.pi * 0.005 * t))
        t += dt
        time.sleep(dt)

# ---------- BLE loop (runs in background thread, live mode only) ----------

async def ble_loop():
    from bleak import BleakClient, BleakScanner
    print(f"Scanning for '{DEVICE_NAME}' ...")
    device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=15.0)
    if device is None:
        print(f"ERROR: '{DEVICE_NAME}' not found. Make sure it is advertising.")
        return

    print(f"Found {device.name} ({device.address}) — connecting ...")
    async with BleakClient(device) as client:
        # Short settle delay — Windows WinRT BLE can reject a CCCD write if it
        # arrives while MTU negotiation / connection-parameter update is still
        # in progress (WinError -2147023673).
        await asyncio.sleep(0.2)
        print("Connected. Subscribing to IMU characteristic ...")
        await client.start_notify(IMU_CHAR_UUID, imu_callback)
        print("Streaming — close the plot window to stop.")
        while True:
            await asyncio.sleep(1)

def start_ble():
    asyncio.run(ble_loop())

# ---------- Matplotlib animation ----------

fig, ax = plt.subplots(figsize=(10, 5))
fig.patch.set_facecolor("#1a1a2e")
ax.set_facecolor("#16213e")

t_vals = list(range(-WINDOW + 1, 1))   # relative time axis in samples

line_x, = ax.plot(t_vals, list(xs), color="#e94560", linewidth=1.2, label="X")
line_y, = ax.plot(t_vals, list(ys), color="#0f9b8e", linewidth=1.2, label="Y")
line_z, = ax.plot(t_vals, list(zs), color="#f5a623", linewidth=1.2, label="Z")

ax.set_xlim(-WINDOW + 1, 0)
ax.set_ylim(-2000, 2000)          # ±2 g expressed in mg
ax.set_xlabel("Samples (25 Hz)", color="#cccccc")
ax.set_ylabel("Acceleration (mg)", color="#cccccc")
ax.set_title("LIS2DW12 - Live Acceleration", color="#ffffff", fontsize=13)
ax.tick_params(colors="#cccccc")
for spine in ax.spines.values():
    spine.set_edgecolor("#444466")
ax.legend(loc="upper left", facecolor="#1a1a2e", edgecolor="#444466",
          labelcolor="#ffffff")
ax.axhline(0, color="#444466", linewidth=0.8, linestyle="--")

temp_text = ax.text(0.98, 0.97, "", transform=ax.transAxes,
                    ha="right", va="top", color="#ffffff",
                    fontsize=11, fontfamily="monospace",
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="#0d0d1a",
                              edgecolor="#444466", alpha=0.8))

def animate(_frame):
    data_x = list(xs)
    data_y = list(ys)
    data_z = list(zs)
    line_x.set_ydata(data_x)
    line_y.set_ydata(data_y)
    line_z.set_ydata(data_z)

    # Auto-scale Y with 10 % padding around current window
    lo = min(min(data_x), min(data_y), min(data_z))
    hi = max(max(data_x), max(data_y), max(data_z))
    pad = max(50, (hi - lo) * 0.1)
    ax.set_ylim(lo - pad, hi + pad)

    t = temp_cdeg[0]
    temp_text.set_text(f"Temp: {t // 100}.{abs(t) % 100:02d} °C")
    return line_x, line_y, line_z, temp_text

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true",
                        help="Run with synthetic data (no hardware needed)")
    args = parser.parse_args()
    if args.test:
        TEST_MODE = True

    stop_evt = threading.Event()

    if TEST_MODE:
        print("[TEST] Synthetic IMU data — no hardware needed.")
        bg = threading.Thread(target=test_data_thread, args=(stop_evt,), daemon=True)
    else:
        print(f"[BLE] Connecting to '{DEVICE_NAME}' ...")
        bg = threading.Thread(target=start_ble, daemon=True)

    bg.start()

    ani = animation.FuncAnimation(fig, animate, interval=40,   # ~25 Hz
                                  blit=True, cache_frame_data=False)
    plt.tight_layout()
    try:
        plt.show()
    finally:
        stop_evt.set()
