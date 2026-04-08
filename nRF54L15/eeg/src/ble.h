#ifndef BLE_H
#define BLE_H

#include <stdbool.h>
#include <stdint.h>
#include "imu.h"

/**
 * Initialize BLE stack and start advertising as "EEG Wearable".
 * Must be called once at startup before any notify calls.
 */
int  ble_init(void);

/** True if a central is currently connected. */
bool ble_is_connected(void);

/** True if the central has subscribed to EEG data notifications. */
bool ble_eeg_subscribed(void);

/**
 * Send raw bytes via the EEG Data NOTIFY characteristic.
 * @param data  Pointer to payload bytes.
 * @param len   Number of bytes (keep <= ATT_MTU - 3 = 244 bytes).
 * @return 0 on success, negative errno on error.
 */
int  ble_notify_eeg(const uint8_t *data, uint16_t len);

/** True if the central has subscribed to IMU data notifications. */
bool ble_imu_subscribed(void);

/**
 * Send one IMU sample via the IMU Data NOTIFY characteristic.
 * Packet: 8 bytes little-endian (x_mg, y_mg, z_mg, temp_cdeg as int16).
 * @return 0 on success, negative errno on error.
 */
int  ble_notify_imu(const struct imu_sample *sample);

/** True if the central has subscribed to Device Status notifications. */
bool ble_status_subscribed(void);

/**
 * Send device status via the Device Status NOTIFY characteristic.
 * Packet format (4 bytes):
 *   [0:1]  uint16_t LE  battery voltage in mV  (0 if VBAT unavailable)
 *   [2]    uint8_t      battery state-of-charge percent  0–100
 *   [3]    uint8_t      PMIC flags:  bit 0 = charging,  bit 1 = error
 *
 * @param vbat_mv   Battery voltage in millivolts.
 * @param pct       State-of-charge 0–100.
 * @param charging  True when nPM1100 CHG pin is asserted (actively charging).
 * @param error     True when nPM1100 ERR pin is asserted (fault condition).
 * @return 0 on success, negative errno on error.
 */
int  ble_notify_status(uint16_t vbat_mv, uint8_t pct, bool charging, bool error);

#endif /* BLE_H */
