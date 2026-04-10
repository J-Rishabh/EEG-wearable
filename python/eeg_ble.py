"""
eeg_ble.py — BLE client for the nRF54L15 + ADS1299 EEG wearable
================================================================
Single source of truth for BLE connection, packet parsing, and control
commands.  Used by eeg_stream_pg.py and BCI experiment scripts.

Packet format (198 bytes)
-------------------------
  Bytes 0–1  : sample index, uint16 LE (increments by 8 per packet)
  Bytes 2–5  : gains[0..3], one uint8 per ADS1299 gain group
  Bytes 6–197: 8 samples × 8 channels × 3 bytes, int24 BE

Gain groups
-----------
  Group 0 → CH1       (EOG)
  Group 1 → CH2, CH3  (EMG / ECG)
  Group 2 → CH4, CH6  (EEG occipital, EEG frontal)
  Group 3 → CH5       (EEG central)
  CH7 powered down, CH8 BIAS — gain treated as 1

Control commands  (CTRL_CHAR_UUID, 2 bytes)
-------------------------------------------
  [group, gain]      — set PGA gain for one group (groups 0-3)
  [0xFF, 0|1]        — ADS1299 internal test signal off/on
  [0xFE, 0|1]        — DRL/BIAS circuit off/on

Usage
-----
    from eeg_ble import BleEEGClient

    def on_samples(uv, gains, rails):
        # uv:    np.ndarray (8, 8) float64 µV  [sample × channel]
        # gains: list[int] len 4 — PGA gain per group
        # rails: np.ndarray (8,) bool — True if any sample hit rail
        eog = uv[:, 0]

    def on_pmic(vbat_mv, pct, charging, error):
        print(f"Battery: {vbat_mv} mV  {pct}%  charging={charging}")

    client = BleEEGClient()
    client.set_sample_callback(on_samples)
    client.set_pmic_callback(on_pmic)
    client.start()   # non-blocking background thread
    ...
    client.send_gain(0, 24)     # set EOG channel gain to 24
    client.send_drl(True)       # enable DRL
    client.send_test_mode(True) # ADS1299 internal square wave
    ...
    client.stop()
"""

import asyncio
import struct
import threading
import numpy as np

# ── Wire constants ─────────────────────────────────────────────────────────────

DEVICE_NAME      = "EEG Wearable"
EEG_CHAR_UUID    = "12340002-1234-1234-1234-123456789abc"
IMU_CHAR_UUID    = "12340003-1234-1234-1234-123456789abc"
CTRL_CHAR_UUID   = "12340004-1234-1234-1234-123456789abc"
STATUS_CHAR_UUID = "12340005-1234-1234-1234-123456789abc"

EEG_VREF_UV         = 4_500_000   # ADS1299 internal reference in µV
EEG_CHANNELS        = 8
EEG_SAMPLES_PER_PKT = 8           # samples batched per BLE notification
EEG_PACKET_LEN      = 198         # 2 idx + 4 gains + 8*8*3 bytes

# ch_to_group[ch] = gain group index (0-indexed).
# -1 = unused (gain treated as 1): CH7 powered down, CH8 BIAS.
_CH_TO_GROUP = [0, 1, 1, 2, 3, 2, -1, -1]

# 90 % of int24 full-scale — flag as railing above this threshold
_RAIL_THRESHOLD = int(0.90 * 8_388_607)


# ── Packet parser ──────────────────────────────────────────────────────────────

def parse_packet(data: bytes):
    """
    Parse a 198-byte ADS1299 BLE packet.

    Returns (idx, gains, uv, rails) or None if data is too short.
      idx   : int               — firmware sample index
      gains : list[int] len 4   — PGA gain per group
      uv    : np.ndarray (8, 8) float64 µV  [sample × channel]
      rails : np.ndarray (8,)   bool — True if any sample on that ch hit rail
    """
    if len(data) < EEG_PACKET_LEN:
        return None
    idx   = struct.unpack_from("<H", data, 0)[0]
    gains = [data[2], data[3], data[4], data[5]]
    raw8  = np.frombuffer(data[6:], dtype=np.uint8).reshape(
        EEG_SAMPLES_PER_PKT, EEG_CHANNELS, 3)
    vals  = np.zeros((EEG_SAMPLES_PER_PKT, EEG_CHANNELS), dtype=np.int32)
    for s in range(EEG_SAMPLES_PER_PKT):
        for ch in range(EEG_CHANNELS):
            b = raw8[s, ch]
            v = (int(b[0]) << 16) | (int(b[1]) << 8) | int(b[2])
            if v & 0x800000:
                v -= 0x1000000
            vals[s, ch] = v
    ch_gains = np.array([
        gains[_CH_TO_GROUP[ch]] if _CH_TO_GROUP[ch] >= 0 else 1
        for ch in range(EEG_CHANNELS)
    ], dtype=np.float64)
    uv    = vals.astype(np.float64) * (EEG_VREF_UV / ch_gains / 8_388_608.0)
    rails = np.any(np.abs(vals) > _RAIL_THRESHOLD, axis=0)
    return idx, gains, uv, rails


# ── BLE client ─────────────────────────────────────────────────────────────────

class BleEEGClient:
    """
    Non-blocking BLE client for the EEG Wearable.

    Callbacks
    ---------
    sample_callback(uv, gains, rails)
        Called on every BLE notification from the BLE thread.
        uv:    np.ndarray (8, 8) float64 µV  [sample × channel]
        gains: list[int] len 4
        rails: np.ndarray (8,) bool

    status_callback(msg: str)
        Optional.  Human-readable connection status / debug strings.

    pmic_callback(vbat_mv: int, pct: int, charging: bool, error: bool)
        Optional.  Battery / PMIC status from the Status characteristic
        (firmware sends this every ~5 s).
    """

    def __init__(self):
        self.connected       = False
        self.sample_callback = None
        self.status_callback = None
        self.pmic_callback   = None
        self.imu_callback    = None

        self._stop           = False      # set True by stop()
        self._loop           = None       # asyncio event loop (BLE thread); used for thread-safe writes
        self._client         = None       # active BleakClient while connected
        self._thread         = None

        # Firmware state — resynced to device on every successful connect so
        # that gain / DRL changes made before a power cycle / reconnect survive.
        self._gain       = [24, 24, 24, 24]
        self._drl_active = True

        # Gap detection / debug counters
        self._last_idx       = -1
        self._pkt_count      = 0
        self._first_pkt_flag = False

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_sample_callback(self, cb):
        """cb(uv: np.ndarray[8,8], gains: list[int], rails: np.ndarray[8])"""
        self.sample_callback = cb

    def set_status_callback(self, cb):
        """cb(msg: str)"""
        self.status_callback = cb

    def set_imu_callback(self, cb):
        """cb(x_mg: int, y_mg: int, z_mg: int, temp_cdeg: int)"""
        self.imu_callback = cb

    def set_pmic_callback(self, cb):
        """cb(vbat_mv: int, pct: int, charging: bool, error: bool)"""
        self.pmic_callback = cb

    def start(self):
        """Start BLE scanning / connection in a background daemon thread."""
        self._stop = False
        self._first_pkt_flag = False
        self._thread = threading.Thread(
            target=self._run_thread, daemon=True, name="ble-eeg")
        self._thread.start()

    def stop(self):
        """Signal the BLE thread to exit cleanly."""
        self._stop     = True
        self.connected = False

    def send_gain(self, group: int, value: int):
        """Set PGA gain for one group.  Thread-safe; no-op if not connected."""
        self._gain[group] = value
        self._post(self._write_gain(group, value))

    def send_test_mode(self, enable: bool):
        """Toggle the ADS1299 internal calibration square wave.  Thread-safe."""
        self._post(self._write_test_mode(enable))

    def send_drl(self, enable: bool):
        """Enable or disable the DRL/BIAS circuit.  Thread-safe."""
        self._drl_active = enable
        self._post(self._write_drl(enable))

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _post(self, coro):
        """Schedule a coroutine on the BLE asyncio loop from any thread."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _run_thread(self):
        import traceback
        try:
            asyncio.run(self._ble_loop())
        except Exception:
            print("[BLE] Thread crashed:")
            traceback.print_exc()

    # ── Notification handlers ──────────────────────────────────────────────────

    def _notify_eeg(self, sender, data: bytearray):
        self._first_pkt_flag = True
        self._pkt_count += 1
        n = self._pkt_count

        # Always print first packet; then every 250 (~8 s at 31 pkt/s) as heartbeat
        if n == 1 or n % 250 == 0:
            print(f"[DBG] notify #{n}  len={len(data)}")

        parsed = parse_packet(bytes(data))
        if parsed is None:
            print(f"[DBG] parse_packet returned None — len={len(data)}")
            return

        idx, gains, uv, rails = parsed

        if n == 1:
            print(f"[DBG] parsed ok  idx={idx}  gains={gains}  "
                  f"uv[0,0..3]={uv[0,:4].round(1)}")

        # Gap detection — firmware index increments by 8 per packet (8 samples/batch)
        if self._last_idx >= 0:
            expected = (self._last_idx + EEG_SAMPLES_PER_PKT) & 0xFFFF
            if idx != expected:
                dropped = (idx - self._last_idx - EEG_SAMPLES_PER_PKT) & 0xFFFF
                print(f"[BLE] Gap: expected idx={expected}, got {idx} "
                      f"(~{dropped} samples dropped)")
        self._last_idx = idx

        if self.sample_callback:
            self.sample_callback(uv, gains, rails)

    def _notify_imu(self, sender, data: bytearray):
        if len(data) < 8:
            return
        x, y, z, t = struct.unpack_from("<4h", data)
        if self.imu_callback:
            self.imu_callback(x, y, z, t)

    def _notify_status(self, sender, data: bytearray):
        """Parse the 4-byte Device Status characteristic packet from firmware."""
        if len(data) < 4:
            return
        vbat_mv  = struct.unpack_from("<H", data, 0)[0]
        pct      = data[2]
        flags    = data[3]
        if self.pmic_callback:
            self.pmic_callback(
                vbat_mv,
                pct,
                bool(flags & 0x01),   # charging
                bool(flags & 0x02),   # error
            )

    # ── GATT write helpers (must be called from BLE loop) ─────────────────────

    async def _write_gain(self, group: int, value: int):
        if self._client and self._client.is_connected:
            await self._client.write_gatt_char(
                CTRL_CHAR_UUID, bytes([group, value]), response=False)

    async def _write_test_mode(self, enable: bool):
        if self._client and self._client.is_connected:
            # Byte 0 = 0xFF → test mode command; byte 1 = 1 enable / 0 disable
            await self._client.write_gatt_char(
                CTRL_CHAR_UUID, bytes([0xFF, 0x01 if enable else 0x00]), response=False)

    async def _write_drl(self, enable: bool):
        if self._client and self._client.is_connected:
            # Byte 0 = 0xFE → DRL toggle command; byte 1 = 1 enable / 0 disable
            await self._client.write_gatt_char(
                CTRL_CHAR_UUID, bytes([0xFE, 0x01 if enable else 0x00]), response=False)

    async def _sync_state(self):
        """
        Resync gain + DRL state to firmware on every successful connect.
        Firmware re-inits to defaults (gain=24, DRL=on) on every power cycle,
        so if the user changed gain or disabled DRL before the device restarted,
        the two sides would be out of sync without this.  Also handles the case
        where the user disabled DRL before a BLE drop — sending it here restores
        the intended state without requiring a button re-press.
        """
        for gi in range(4):
            await self._write_gain(gi, self._gain[gi])
        await self._write_drl(self._drl_active)

    # ── Main BLE loop ──────────────────────────────────────────────────────────

    async def _ble_loop(self):
        from bleak import BleakScanner, BleakClient

        # Store loop so send_* methods can post coroutines from the Qt thread
        self._loop = asyncio.get_event_loop()

        self._log(f"[BLE] Scanning for '{DEVICE_NAME}' …")
        dev = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=20.0)
        if dev is None:
            self._log("[BLE] Not found.")
            return

        self._log(f"[BLE] Connecting to {dev.address} …")

        for conn_attempt in range(20):
            if self._stop:
                break

            # Reset data-arrival flag for this connection attempt
            self._first_pkt_flag = False

            try:
                async with BleakClient(dev, use_cached_services=False) as client:
                    self._client = client

                    if conn_attempt == 0:
                        print("[DBG] Services / characteristics:")
                        for svc in client.services:
                            print(f"  SVC {svc.uuid}")
                            for ch in svc.characteristics:
                                print(f"    CHAR {ch.uuid}  props={ch.properties}")

                    # Wait 1.0 s for LL procedures (DLE, conn param update) to finish.
                    # 0x23 LL_PROC_COLLISION typically occurs within ~0.8 s of connect.
                    await asyncio.sleep(1.0)
                    if not client.is_connected:
                        self._log(f"[BLE] conn {conn_attempt+1}: dropped during settle — retrying")
                        self._client = None
                        continue

                    # Windows WinRT cancels CCCD write if MTU exchange is still in
                    # progress (WinError -2147023673). Retry a few times within the
                    # same connection — reconnecting each attempt makes things worse.
                    subscribed = False
                    for sub_attempt in range(4):
                        try:
                            if sub_attempt > 0:
                                await asyncio.sleep(0.5)
                            if not client.is_connected:
                                break
                            await client.start_notify(EEG_CHAR_UUID, self._notify_eeg)
                            subscribed = True
                            # Best-effort subscribe to status — firmware may be older
                            try:
                                await client.start_notify(STATUS_CHAR_UUID, self._notify_status)
                                self._log("[BLE] Status characteristic subscribed")
                            except Exception as se:
                                self._log(f"[BLE] Status char not available (old firmware?): {se}")
                            # Best-effort subscribe to IMU
                            try:
                                await client.start_notify(IMU_CHAR_UUID, self._notify_imu)
                                self._log("[BLE] IMU characteristic subscribed")
                            except Exception as ie:
                                self._log(f"[BLE] IMU char not available: {ie}")
                            break
                        except Exception as e:
                            self._log(f"[BLE] start_notify {sub_attempt+1}/4 failed: {e}")

                    if not subscribed:
                        self._log(f"[BLE] conn {conn_attempt+1}: could not subscribe — reconnecting")
                        self._client = None
                        continue

                    # Verify data actually arrives — Windows sometimes silently drops
                    # the CCCD write so start_notify returns success but firmware never
                    # enables notifications.
                    self._log("[BLE] Subscribed — waiting for first packet …")
                    for _ in range(100):   # 10 s timeout (100 × 0.1 s) — DLE takes ~3 s after CCCD, firmware streams ~2 s later
                        await asyncio.sleep(0.1)
                        if self._first_pkt_flag:
                            break
                        if not client.is_connected:
                            break
                    else:
                        self._log(f"[BLE] conn {conn_attempt+1}: no data in 10 s "
                                  f"(CCCD not delivered?) — reconnecting")
                        self._client = None
                        continue

                    if not self._first_pkt_flag:
                        # Connection dropped before data arrived
                        self._log(f"[BLE] conn {conn_attempt+1}: dropped before data — retrying")
                        self._client = None
                        continue

                    # Sync Python state → firmware on every connect.
                    await self._sync_state()

                    self.connected = True
                    self._log("[BLE] Streaming.")

                    while not self._stop and client.is_connected:
                        await asyncio.sleep(0.1)

                    try:
                        await client.stop_notify(EEG_CHAR_UUID)
                    except Exception:
                        pass
                    try:
                        await client.stop_notify(STATUS_CHAR_UUID)
                    except Exception:
                        pass
                    try:
                        await client.stop_notify(IMU_CHAR_UUID)
                    except Exception:
                        pass

                    self._client   = None
                    self.connected = False

                    if self._stop:
                        break
                    self._log("[BLE] Connection dropped — reconnecting")

            except Exception as e:
                self._client   = None
                self.connected = False
                self._log(f"[BLE] conn {conn_attempt+1}/20 exception: {e}")

            await asyncio.sleep(0.5)

        self._client   = None
        self.connected = False
        self._log("[BLE] Stopped.")

    def _log(self, msg: str):
        print(msg)
        if self.status_callback:
            self.status_callback(msg)
