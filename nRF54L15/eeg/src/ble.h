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

#endif /* BLE_H */
