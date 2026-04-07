#ifndef VBAT_H
#define VBAT_H

#include <stdint.h>

/* ---------- Hardware notes ----------
 *
 * Resistor divider on P1.13 (AIN7):
 *   R_top  = 2 MΩ  (battery positive → mid-node)
 *   R_bot  = 1 MΩ  (mid-node → GND)
 *   Ratio  = 1/3  →  V_adc = V_bat / 3  →  V_bat = V_adc × 3
 *
 * LiPo cell (200 mAh):
 *   Full charge cutoff : 4200 mV  (100 %)
 *   Low-battery cutoff : 3000 mV  (  0 %)
 *   State-of-charge estimated via a lookup table that follows the
 *   typical non-linear LiPo discharge curve.
 */

/**
 * Set up the SAADC channel for VBAT sensing.
 * Must be called once at startup.
 * @return 0 on success, negative errno on failure.
 */
int vbat_init(void);

/**
 * Read the battery voltage.
 * @param mv_out  Battery voltage in millivolts.
 * @return 0 on success, negative errno on failure.
 */
int vbat_read_mv(uint32_t *mv_out);

/**
 * Estimate battery state-of-charge.
 * @param pct_out  Percentage 0–100.
 * @return 0 on success, negative errno on failure.
 */
int vbat_percent(uint8_t *pct_out);

#endif /* VBAT_H */
