#ifndef PMIC_H
#define PMIC_H

#include <stdbool.h>

/* ---------- Hardware notes ----------
 *
 * nPM1100 status outputs (both open-drain, active-low, pulled up on board):
 *
 *   P0.02  PMIC_ERR  — asserted (LOW) on charging fault:
 *                       over-voltage, over-temperature, charge-timeout, or
 *                       reverse-current.  Clears when VBUS disconnects/reconnects.
 *
 *   P0.03  PMIC_CHG  — asserted (LOW) while the charger is actively pushing
 *                       current into the cell.  De-asserts when the cell is full
 *                       (CV phase complete) OR when VBUS is absent.
 *
 * Limitation: CHG=HIGH is ambiguous — it means either "fully charged" or
 * "USB disconnected".  The nPM1100 has no VBUS-detect output pin, so the
 * firmware cannot distinguish these two states.  Future PCB revision should
 * add a VBUS sense pin (e.g. via voltage divider or simple comparator).
 *
 * USB D+/D-: connected only to the nPM1100 for BC1.2 charger detection
 * (SDP=100mA, DCP/CDP=500mA).  Handled autonomously by the PMIC — the MCU
 * has no access to the detected charger type.
 */

/**
 * Configure P0.02 (PMIC_ERR) and P0.03 (PMIC_CHG) as GPIO inputs.
 * Pull-ups are specified in the devicetree node; this just enables the pin.
 * @return 0 on success, negative errno on failure.
 */
int pmic_init(void);

/** True while the nPM1100 is actively charging (PMIC_CHG pin asserted / LOW). */
bool pmic_is_charging(void);

/** True when the nPM1100 has flagged a charging fault (PMIC_ERR pin asserted / LOW). */
bool pmic_is_error(void);

#endif /* PMIC_H */
