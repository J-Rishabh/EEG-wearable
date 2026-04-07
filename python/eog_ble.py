"""
eog_ble.py — BLE connection and EEG packet parsing
====================================================
Connects to "EEG Wearable", subscribes to the EEG characteristic,
and calls a registered callback with parsed channel data on every packet.

Supports both:
  - Current stub packet  (4 bytes — big-endian counter)
  - Real ADS1299 packet  (195 bytes — see PLAN.md Plan 1.9)

Usage:
    client = BleEEGClient()
    client.set_sample_callback(my_cb)   # my_cb(samples: np.ndarray[N,8], gain: int)
    client.start()                       # non-blocking — runs BLE in background thread
    ...
    client.stop()
"""

import asyncio
import struct
import threading
import numpy as np
from bleak import BleakClient, BleakScanner

# ---------- Config ----------

DEVICE_NAME   = "EEG Wearable"
EEG_CHAR_UUID = "12340002-1234-1234-1234-123456789abc"

# ADS1299 constants (from PLAN.md)
EEG_VREF_UV           = 4_500_000   # internal reference 4.5 V in µV
EEG_CHANNELS          = 8
EEG_SAMPLES_PER_PKT   = 8           # samples batched per BLE notification
EEG_PACKET_FULL_LEN   = 195         # 2 idx + 1 gain + 8*8*3 bytes
EEG_PACKET_STUB_LEN   = 4           # counter stub (firmware pre-ADS1299)


class BleEEGClient:
    """
    Non-blocking BLE client for the EEG Wearable.

    Callbacks
    ---------
    sample_callback(samples, gain)
        Called on every packet from the BLE notification thread.
        samples : np.ndarray, shape (N, 8), dtype float32  — µV per channel
        gain    : int — current ADS1299 PGA gain (1/2/4/6/8/12/24)

    status_callback(msg: str)
        Optional. Called with human-readable status strings (connect, error…)
    """

    def __init__(self):
        self.connected       = False
        self.sample_callback = None
        self.status_callback = None

        self._gain    = 24
        self._stop    = threading.Event()
        self._thread  = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_sample_callback(self, cb):
        """cb(samples: np.ndarray[N, 8 ch], gain: int)  — µV per channel."""
        self.sample_callback = cb

    def set_status_callback(self, cb):
        """cb(msg: str)  — connection status updates."""
        self.status_callback = cb

    def start(self):
        """Start BLE in a background daemon thread."""
        self._stop.clear()
        self._thread = threading.Thread(
            target=lambda: asyncio.run(self._ble_loop()),
            daemon=True,
            name="ble-eeg")
        self._thread.start()

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------------
    # Packet parsing
    # ------------------------------------------------------------------

    def _parse_packet(self, data: bytes):
        n = len(data)

        if n == EEG_PACKET_FULL_LEN:
            # ---- Real ADS1299 packet ----
            # Byte 0-1 : sample index (uint16 LE) — gap detection
            # Byte 2   : gain (embedded by firmware every packet)
            # Bytes 3+ : EEG_SAMPLES_PER_PKT × EEG_CHANNELS × 3 bytes, int24 BE
            gain = data[2]
            self._gain = gain

            raw = np.frombuffer(data[3:], dtype=np.uint8).reshape(
                EEG_SAMPLES_PER_PKT, EEG_CHANNELS, 3)

            samples = np.empty((EEG_SAMPLES_PER_PKT, EEG_CHANNELS), dtype=np.float32)
            for s in range(EEG_SAMPLES_PER_PKT):
                for ch in range(EEG_CHANNELS):
                    b = raw[s, ch]
                    v = (int(b[0]) << 16) | (int(b[1]) << 8) | int(b[2])
                    if v & 0x800000:      # sign-extend 24-bit → 32-bit
                        v -= 0x1000000
                    samples[s, ch] = v * (EEG_VREF_UV / gain / 8_388_608.0)

            if self.sample_callback:
                self.sample_callback(samples, gain)

        elif n == EEG_PACKET_STUB_LEN:
            # ---- Current counter stub (pre-ADS1299 firmware) ----
            # Just fire the callback with zeros so downstream pipeline keeps running.
            # Counter value available for debugging if needed:
            # counter = struct.unpack_from(">I", data)[0]
            if self.sample_callback:
                self.sample_callback(
                    np.zeros((1, EEG_CHANNELS), dtype=np.float32),
                    self._gain)

        else:
            # Unknown packet length — log and ignore
            self._status(f"[BLE] unexpected packet length {n} — ignoring")

    def _notification_handler(self, sender, data: bytearray):
        """Called by bleak on the asyncio thread for every BLE notification."""
        self._parse_packet(bytes(data))

    # ------------------------------------------------------------------
    # BLE loop
    # ------------------------------------------------------------------

    async def _ble_loop(self):
        self._status(f"[BLE] Scanning for '{DEVICE_NAME}' ...")

        while not self._stop.is_set():
            try:
                device = await BleakScanner.find_device_by_name(
                    DEVICE_NAME, timeout=10.0)

                if device is None:
                    self._status(f"[BLE] '{DEVICE_NAME}' not found — retrying ...")
                    await asyncio.sleep(2.0)
                    continue

                self._status(f"[BLE] Found {device.address} — connecting ...")

                async with BleakClient(device) as client:
                    # Settle before CCCD write (Windows WinRT timing)
                    await asyncio.sleep(0.2)
                    await client.start_notify(EEG_CHAR_UUID,
                                              self._notification_handler)
                    self.connected = True
                    self._status("[BLE] Connected — streaming EEG.")

                    while not self._stop.is_set() and client.is_connected:
                        await asyncio.sleep(0.2)

                self.connected = False
                self._status("[BLE] Disconnected — reconnecting ...")
                await asyncio.sleep(1.0)

            except Exception as e:
                self.connected = False
                self._status(f"[BLE] Error: {e} — retrying ...")
                await asyncio.sleep(2.0)

        self._status("[BLE] Stopped.")

    def _status(self, msg: str):
        print(msg)
        if self.status_callback:
            self.status_callback(msg)
