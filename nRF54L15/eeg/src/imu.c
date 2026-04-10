#include "imu.h"

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(imu, LOG_LEVEL_INF);

/* ── LIS2DW12 I2C address ────────────────────────────────────────────────────
 * SA0/ADDR pin pulled low on this board → 0x18.
 * If nothing is found at 0x18, run `i2c scan i2c@40095000` in the RTT shell
 * to check whether the chip is responding at 0x19 (SA0 high) instead.  */
#define LIS2DW12_ADDR  0x18

/* ── Register map (LIS2DW12 datasheet DS12591) ───────────────────────────────*/
#define REG_OUT_T_L    0x0D
#define REG_WHO_AM_I   0x0F   /* expected: 0x44 */
#define REG_CTRL1      0x20
#define REG_CTRL2      0x21
#define REG_CTRL6      0x25
#define REG_STATUS     0x27
#define REG_OUT_X_L    0x28   /* 6 bytes: X_L X_H Y_L Y_H Z_L Z_H */

/* ── CTRL1 — ODR + power mode ────────────────────────────────────────────────
 * Bits [7:4] = ODR: 0100 = 50 Hz
 * Bits [3:2] = MODE: 00   = Low-Power
 * Bits [1:0] = LP_MODE: 00 = LP Mode 1 (12-bit, ~3 µA @ 50 Hz)
 * → 0100_0000 = 0x40 */
#define CTRL1_50HZ_LP1  0x40

/* ── CTRL2 ───────────────────────────────────────────────────────────────────
 * Bit 6 = SOFT_RESET (self-clearing, ~1 ms)
 * Bit 3 = BDU — Block Data Update: prevents reading high/low byte of different
 *         samples; mandatory for reliable burst reads at 50 Hz
 * Bit 2 = IF_ADD_INC — auto-increment register address on multi-byte read */
#define CTRL2_RESET     0x40
#define CTRL2_INIT      0x0C  /* BDU=1, IF_ADD_INC=1 */

/* ── CTRL6 — full-scale + filter ─────────────────────────────────────────────
 * Bits [5:4] = FS: 00 = ±2 g (0.976 mg/LSB in LP Mode 1)
 * All other bits = 0 (power-on default) */
#define CTRL6_FS_2G     0x00

/* ── Sensitivity ─────────────────────────────────────────────────────────────
 * LP Mode 1, ±2 g: 0.976 mg/LSB (12-bit left-aligned value after >> 4).
 * Integer approximation: × 976, ÷ 1000. */
#define SENS_NUM  976
#define SENS_DEN  1000

/* ── Temperature ─────────────────────────────────────────────────────────────
 * OUT_T_L/H is a 12-bit left-aligned signed value.
 * 1 LSB of the 12-bit value = 1/16 °C; offset = +25 °C at raw = 0.
 * (Zephyr driver: LIS2DW12_TEMP_SCALE_FACTOR = 62500 µ°C = 1000000/16) */

static const struct device *i2c_dev;
static bool ready;

/* ── Helpers ─────────────────────────────────────────────────────────────────*/

/* Probe an I2C address by reading WHO_AM_I — returns the register value (≥0)
 * or a negative errno if the device NACKed or the read failed. */
static int probe_who_am_i(uint8_t addr)
{
    uint8_t reg = REG_WHO_AM_I;
    uint8_t val = 0;
    int ret = i2c_write_read(i2c_dev, addr, &reg, 1, &val, 1);
    return (ret == 0) ? (int)val : ret;
}

static int reg_write(uint8_t reg, uint8_t val)
{
    uint8_t buf[2] = {reg, val};
    return i2c_write(i2c_dev, buf, sizeof(buf), LIS2DW12_ADDR);
}

static int reg_read(uint8_t reg, uint8_t *out)
{
    return i2c_write_read(i2c_dev, LIS2DW12_ADDR, &reg, 1, out, 1);
}

static int reg_burst_read(uint8_t start_reg, uint8_t *buf, uint8_t len)
{
    return i2c_write_read(i2c_dev, LIS2DW12_ADDR, &start_reg, 1, buf, len);
}

/* ── Public API ──────────────────────────────────────────────────────────────*/

int imu_init(void)
{
    /* Get the I2C bus controller — bypasses the Zephyr LIS2DW12 sensor driver
     * and talks to the chip directly, eliminating driver init assumptions. */
    i2c_dev = DEVICE_DT_GET(DT_NODELABEL(i2c22));
    if (!device_is_ready(i2c_dev)) {
        LOG_ERR("i2c22 bus not ready");
        return -ENODEV;
    }
    LOG_INF("i2c22 bus ready — recovering bus then probing LIS2DW12");

    /* Bus recovery: send 9 SCL pulses to un-stick any device that was left
     * holding SDA low by a previous incomplete transaction (e.g. the Zephyr
     * LIS2DW12 driver probe that runs at ~0.5 s before main() starts). */
    int rc = i2c_recover_bus(i2c_dev);
    LOG_INF("i2c_recover_bus: %d (%s)", rc, rc == 0 ? "OK" : "failed");

    /* Continuous probe loop — repeats every 200 ms so a scope can trigger on
     * the I2C activity without needing to catch a one-shot event.
     * Exits as soon as either address ACKs. */
    LOG_INF("Probing 0x18 / 0x19 every 200 ms (max 25 attempts = 5 s)");
    int who18 = -EIO, who19 = -EIO;
    for (int attempt = 0; attempt < 25; attempt++) {
        who18 = probe_who_am_i(0x18);
        who19 = probe_who_am_i(0x19);
        LOG_INF("  [%2d] 0x18: %s  0x19: %s", attempt,
                who18 >= 0 ? "ACK" : "nack",
                who19 >= 0 ? "ACK" : "nack");
        if (who18 >= 0 || who19 >= 0) {
            break;
        }
        k_msleep(200);
    }
    if (who18 < 0 && who19 < 0) {
        LOG_ERR("LIS2DW12 not found at 0x18 or 0x19 after 5 s — IMU unavailable");
        return -ENODEV;
    }
    if (who18 >= 0) {
        LOG_INF("Found at 0x18  WHO_AM_I=0x%02X", (uint8_t)who18);
    } else if (who19 >= 0) {
        LOG_WRN("Found at 0x19 (SA0 high) — change LIS2DW12_ADDR to 0x19");
    }

    /* WHO_AM_I — confirms the chip is wired and responding.
     * Expected 0x44.  If you get NACK (-EIO) here, check:
     *   - I2C address: run `i2c scan i2c@40095000` in the RTT shell
     *   - Pull-ups on SDA/SCL
     *   - VDD / power rail */
    uint8_t who = 0;
    int ret = reg_read(REG_WHO_AM_I, &who);
    if (ret < 0) {
        LOG_ERR("WHO_AM_I read failed (err %d) — is LIS2DW12 powered and wired?", ret);
        return ret;
    }
    if (who != 0x44) {
        LOG_ERR("WHO_AM_I = 0x%02X, expected 0x44 — wrong chip or wrong I2C address", who);
        return -ENODEV;
    }
    LOG_INF("WHO_AM_I = 0x44 OK");

    /* Soft reset — brings all registers to power-on defaults.
     * SOFT_RESET bit (CTRL2[6]) is self-clearing; wait 2 ms to be safe.
     * Must issue this in power-down mode (ODR=0), which is the state after
     * power-on, so no need to clear ODR first. */
    ret = reg_write(REG_CTRL2, CTRL2_RESET);
    if (ret < 0) {
        LOG_ERR("CTRL2 soft reset write failed (err %d)", ret);
        return ret;
    }
    k_msleep(2);

    /* Verify reset cleared (SOFT_RESET bit should be 0 now) */
    uint8_t ctrl2 = 0;
    reg_read(REG_CTRL2, &ctrl2);
    if (ctrl2 & 0x40) {
        LOG_WRN("SOFT_RESET bit still set after 2 ms — I2C may be unreliable");
    }

    /* CTRL2: enable BDU + auto-increment (recommended for burst reads) */
    ret = reg_write(REG_CTRL2, CTRL2_INIT);
    if (ret < 0) {
        LOG_ERR("CTRL2 init write failed (err %d)", ret);
        return ret;
    }

    /* CTRL6: full-scale ±2 g, no filter override */
    ret = reg_write(REG_CTRL6, CTRL6_FS_2G);
    if (ret < 0) {
        LOG_ERR("CTRL6 write failed (err %d)", ret);
        return ret;
    }

    /* CTRL1: 50 Hz, LP Mode 1 — starts the sensor */
    ret = reg_write(REG_CTRL1, CTRL1_50HZ_LP1);
    if (ret < 0) {
        LOG_ERR("CTRL1 write failed (err %d)", ret);
        return ret;
    }

    /* Read back CTRL1 to confirm the write landed */
    uint8_t ctrl1 = 0;
    reg_read(REG_CTRL1, &ctrl1);
    if (ctrl1 != CTRL1_50HZ_LP1) {
        LOG_WRN("CTRL1 readback = 0x%02X, expected 0x%02X", ctrl1, CTRL1_50HZ_LP1);
    } else {
        LOG_INF("CTRL1 = 0x%02X OK", ctrl1);
    }

    ready = true;
    LOG_INF("IMU ready (LIS2DW12 @ 0x%02X, raw I2C, 50 Hz, LP Mode 1, ±2 g)",
            LIS2DW12_ADDR);
    return 0;
}

int imu_read(struct imu_sample *out)
{
    if (!ready) {
        return -EAGAIN;
    }

    /* Poll STATUS register for data-ready (bit 0 = DRDY).
     * At 50 Hz a new sample arrives every 20 ms.  We call imu_read at 25 Hz
     * so DRDY should already be set; retry once if it isn't. */
    uint8_t status = 0;
    reg_read(REG_STATUS, &status);
    if (!(status & 0x01)) {
        k_usleep(500);
        reg_read(REG_STATUS, &status);
        if (!(status & 0x01)) {
            /* Return stale data rather than blocking — caller can check return */
            return -EBUSY;
        }
    }

    /* Burst-read X_L…Z_H (6 bytes, auto-increment enabled via CTRL2 IF_ADD_INC).
     * Data is 12-bit left-aligned in a 16-bit little-endian register pair.
     * Right-shift by 4 (arithmetic) to extract the signed 12-bit value. */
    uint8_t raw[6];
    int ret = reg_burst_read(REG_OUT_X_L, raw, sizeof(raw));
    if (ret < 0) {
        return ret;
    }

    int16_t rx = (int16_t)((uint16_t)raw[0] | ((uint16_t)raw[1] << 8));
    int16_t ry = (int16_t)((uint16_t)raw[2] | ((uint16_t)raw[3] << 8));
    int16_t rz = (int16_t)((uint16_t)raw[4] | ((uint16_t)raw[5] << 8));

    /* >> 4 on signed int16_t is arithmetic (sign-extending) in C on all
     * supported Zephyr targets (ARM Cortex-M/A, which mandate arithmetic shift) */
    out->x_mg = (int16_t)(((int32_t)(rx >> 4) * SENS_NUM) / SENS_DEN);
    out->y_mg = (int16_t)(((int32_t)(ry >> 4) * SENS_NUM) / SENS_DEN);
    out->z_mg = (int16_t)(((int32_t)(rz >> 4) * SENS_NUM) / SENS_DEN);

    /* Temperature — 2 bytes starting at OUT_T_L (0x0D).
     * 12-bit left-aligned, 1 LSB of 12-bit value = 1/16 °C, offset +25 °C.
     * (Zephyr ref: LIS2DW12_TEMP_SCALE_FACTOR = 62500 µ°C = 1,000,000/16)
     * Convert to hundredths of °C: × 100 / 16 = × 25 / 4. */
    uint8_t t_raw[2];
    if (reg_burst_read(REG_OUT_T_L, t_raw, 2) == 0) {
        int16_t rt = (int16_t)((uint16_t)t_raw[0] | ((uint16_t)t_raw[1] << 8));
        out->temp_cdeg = (int16_t)(2500 + (int32_t)(rt >> 4) * 100 / 16);
    } else {
        out->temp_cdeg = 0;
    }

    return 0;
}

bool imu_is_ready(void)
{
    return ready;
}
