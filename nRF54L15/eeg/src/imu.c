#include "imu.h"

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(imu, LOG_LEVEL_INF);

/* LIS2DW12 bound via DTS node label "lis2dw12" on i2c22 @ 0x18 */
static const struct device *imu_dev;
static bool ready;

/* ---------- Motion detection ---------- */

static void motion_trigger_handler(const struct device *dev,
                                   const struct sensor_trigger *trig)
{
    ARG_UNUSED(dev);
    ARG_UNUSED(trig);
    LOG_INF("IMU: motion detected");
}

/* ---------- Public API ---------- */

int imu_init(void)
{
    imu_dev = DEVICE_DT_GET(DT_NODELABEL(lis2dw12));
    if (!device_is_ready(imu_dev)) {
        LOG_ERR("IMU (LIS2DW12) not ready - check DTS and I2C wiring");
        return -ENODEV;
    }

    /* Motion-detection trigger: fires when acceleration exceeds the
     * wake-up threshold (configured in DTS via wakeup-threshold property).
     * Requires CONFIG_LIS2DW12_TRIGGER_GLOBAL_THREAD + CONFIG_LIS2DW12_WAKEUP. */
    static struct sensor_trigger motion_trig = {
        .type = SENSOR_TRIG_DELTA,
        .chan = SENSOR_CHAN_ACCEL_XYZ,
    };
    int ret = sensor_trigger_set(imu_dev, &motion_trig, motion_trigger_handler);
    if (ret < 0) {
        /* Not fatal — continue without wakeup trigger (e.g. trigger not compiled in). */
        LOG_WRN("Motion trigger unavailable (err %d) - skipping", ret);
    }

    ready = true;
    LOG_INF("IMU ready (LIS2DW12 @ 0x18, LP Mode 1, 50 Hz, ±2 g)");
    return 0;
}

int imu_read(struct imu_sample *out)
{
    if (!ready) {
        return -EAGAIN;
    }

    /* Fetch all channels from the device in one I2C burst. */
    int ret = sensor_sample_fetch(imu_dev);
    if (ret < 0) {
        return ret;
    }

    /* --- Acceleration ---
     * sensor_channel_get returns m/s² as sensor_value { val1 (integer), val2 (µ fraction) }.
     * Full value in µm/s² = val1 * 1_000_000 + val2.
     * 1 mg = 9.80665 mm/s² = 9806.65 µm/s² ≈ 9807 µm/s².
     * So mg = total_µm_s2 / 9807. */
    struct sensor_value accel[3];
    ret = sensor_channel_get(imu_dev, SENSOR_CHAN_ACCEL_XYZ, accel);
    if (ret < 0) {
        return ret;
    }

    int64_t us2;
    us2 = (int64_t)accel[0].val1 * 1000000LL + accel[0].val2;
    out->x_mg = (int16_t)(us2 / 9807);

    us2 = (int64_t)accel[1].val1 * 1000000LL + accel[1].val2;
    out->y_mg = (int16_t)(us2 / 9807);

    us2 = (int64_t)accel[2].val1 * 1000000LL + accel[2].val2;
    out->z_mg = (int16_t)(us2 / 9807);

    /* --- Temperature ---
     * sensor_value: val1 = °C integer, val2 = fractional µ°C.
     * Convert to hundredths of °C: val1 * 100 + val2 / 10_000. */
    struct sensor_value temp;
    ret = sensor_channel_get(imu_dev, SENSOR_CHAN_DIE_TEMP, &temp);
    if (ret < 0) {
        out->temp_cdeg = 0;
    } else {
        out->temp_cdeg = (int16_t)(temp.val1 * 100 + temp.val2 / 10000);
    }

    return 0;
}

bool imu_is_ready(void)
{
    return ready;
}
