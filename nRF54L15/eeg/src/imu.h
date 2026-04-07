#ifndef IMU_H
#define IMU_H

#include <stdint.h>
#include <stdbool.h>

/* ---------- Operating modes (set via DTS power-mode property) ----------
 *
 * These are documented here for reference — the active mode is baked
 * into the DTS node (power-mode = <0> means LP Mode 1 below).
 *
 * power-mode | Name            | Resolution | Current @ 50 Hz | Noise (RMS)
 * -----------|-----------------|------------|-----------------|------------
 *     0      | LP Mode 1       | 12-bit     | ~3   µA         | 4.5 mg     ← selected
 *     1      | LP Mode 2       | 14-bit     | ~20  µA         | 2.4 mg
 *     2      | LP Mode 3       | 14-bit     | ~35  µA         | 1.8 mg
 *     3      | LP Mode 4       | 14-bit     | ~63  µA         | 1.3 mg
 *     4      | High-Performance| 14-bit     | ~90  µA         | 0.09 mg/√Hz
 *
 * Power-down (ODR = 0): 50 nA — use when EEG is also idle.
 *
 * Full-scale options (set via DTS range property):
 *   range = <2>   → ±2  g   0.976 mg/LSB (LP1) — selected, enough for head motion
 *   range = <4>   → ±4  g   1.952 mg/LSB
 *   range = <8>   → ±8  g   3.904 mg/LSB
 *   range = <16>  → ±16 g   7.808 mg/LSB
 *
 * ODR options (set via DTS odr property, in Hz):
 *   1, 12, 25, 50, 100, 200, 400, 800, 1600
 *   Selected: 50 Hz — fast enough for head motion, low power.
 *
 * Motion detection (SENSOR_TRIG_MOTION):
 *   Wakes from sleep when acceleration exceeds WAKE_UP_THS on any axis.
 *   Useful for duty-cycling: sleep the EEG AFE when no head motion detected.
 */

/* ---------- Data types ---------- */

/* One IMU sample — transmitted over BLE and used by Python visualisations. */
struct imu_sample {
    int16_t x_mg;      /* X acceleration in milligravity (1g = 1000) */
    int16_t y_mg;      /* Y acceleration in milligravity             */
    int16_t z_mg;      /* Z acceleration in milligravity             */
    int16_t temp_cdeg; /* Die temperature in hundredths of °C        */
};

/* ---------- API ---------- */

/**
 * Initialize the LIS2DW12 and configure the motion-detection trigger.
 * Must be called after the Zephyr sensor subsystem is ready (i.e. in main).
 * @return 0 on success, negative errno on failure.
 */
int imu_init(void);

/**
 * Read one sample from the LIS2DW12 (blocking, ~1 I2C transaction).
 * @param out  Pointer to sample struct to fill.
 * @return 0 on success, negative errno on failure.
 */
int imu_read(struct imu_sample *out);

/** True if the IMU device is ready and initialised. */
bool imu_is_ready(void);

#endif /* IMU_H */
