#include <zephyr/kernel.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/logging/log.h>
#include "ble.h"
#include "imu.h"
#include "vbat.h"
#include "eeg.h"
#include "pmic.h"

LOG_MODULE_REGISTER(main, LOG_LEVEL_INF);

#define LED0_NODE DT_ALIAS(led0)
static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(LED0_NODE, gpios);

/* ---------- LED error flag ----------
 *
 * Set once by led_set_error() on any init failure.
 * Sticky - never clears. LED thread enters fast-blink forever.
 */
static ATOMIC_DEFINE(led_error_flag, 1);

static void led_set_error(void)
{
    atomic_set_bit(led_error_flag, 0);
}

/* ---------- LED thread ----------
 *
 * State priority (highest → lowest):
 *   Error       → fast blink  100 ms ON / 100 ms OFF  (sticky)
 *   Connected   → solid on
 *   Advertising → breathing   smooth 0→100→0 % duty over ~3 s
 *
 * To use a heartbeat instead of breathing while advertising:
 *   comment out the "BREATHING" block and uncomment the "HEARTBEAT" block.
 */

#define LED_STACK_SIZE  512
#define LED_PRIORITY    7

/* Breathing: 100 Hz PWM carrier (10 ms period) with 10 brightness levels.
 * 15 carrier cycles per level × 10 levels × 2 halves × 10 ms = 3000 ms/cycle.
 * 100 Hz is above the ~60 Hz human flicker-fusion threshold → no visible blink. */
#define BREATH_PERIOD_MS   10   /* PWM carrier period — must be < 16 ms (>60 Hz) */
#define BREATH_STEPS       10   /* brightness levels: step 0 (off) … 10 (full on) */
#define BREATH_HOLD_CYCLES 15   /* carrier cycles to hold each level */

K_THREAD_STACK_DEFINE(led_stack, LED_STACK_SIZE);
static struct k_thread led_thread_data;

static void led_thread(void *p1, void *p2, void *p3)
{
    ARG_UNUSED(p2);
    ARG_UNUSED(p3);

    const struct gpio_dt_spec *l = p1;
    int  step         = 0;
    bool rising       = true;
    bool was_connected = false;

    while (1) {
        /* --- ERROR: fast blink, never exits --- */
        if (atomic_test_bit(led_error_flag, 0)) {
            gpio_pin_set_dt(l, 1);
            k_msleep(100);
            gpio_pin_set_dt(l, 0);
            k_msleep(100);
            continue;
        }

        /* --- CONNECTED: dim PWM (~30 % duty, 100 Hz) saves current vs solid on --- */
        if (ble_is_connected()) {
            gpio_pin_set_dt(l, 1);
            k_msleep(3);
            gpio_pin_set_dt(l, 0);
            k_msleep(7);
            was_connected = true;
            continue;
        }

        /* Reset breathing phase when coming back from connected */
        if (was_connected) {
            step          = 0;
            rising        = true;
            was_connected = false;
        }

        /* --- ADVERTISING --- */

        /* -- HEARTBEAT (double-pulse ~1 Hz) --
         * Uncomment this block and comment out the BREATHING block to use.
         *
         * gpio_pin_set_dt(l, 1); k_msleep(80);
         * gpio_pin_set_dt(l, 0); k_msleep(80);
         * gpio_pin_set_dt(l, 1); k_msleep(80);
         * gpio_pin_set_dt(l, 0); k_msleep(760);
         * continue;
         */

        /* -- BREATHING: triangle-wave software PWM --
         * step 0 → 0 % duty (off), step BREATH_STEPS → 100 % (full on).
         * Each step holds for BREATH_HOLD_CYCLES × BREATH_PERIOD_MS ms so the
         * PWM carrier runs at 100 Hz — invisible to the naked eye.  */
        int on_ms  = (step * BREATH_PERIOD_MS) / BREATH_STEPS;
        int off_ms = BREATH_PERIOD_MS - on_ms;

        for (int c = 0; c < BREATH_HOLD_CYCLES; c++) {
            if (on_ms > 0) {
                gpio_pin_set_dt(l, 1);
                k_msleep(on_ms);
            }
            if (off_ms > 0) {
                gpio_pin_set_dt(l, 0);
                k_msleep(off_ms);
            }
        }

        if (rising) {
            if (++step >= BREATH_STEPS) rising = false;
        } else {
            if (--step <= 0)            rising = true;
        }
    }
}

/* ---------- Main ---------- */

int main(void)
{
    int ret;
    bool imu_ok  = false;
    bool vbat_ok = false;
    bool eeg_ok  = false;
    bool pmic_ok = false;

    /* ------------------------------------------------------------------ */
    /* Give RTT viewer 3 s to connect before boot logs scroll past.       */
    k_msleep(3000);
    LOG_INF("=== EEG Wearable booting ===");
    /* ------------------------------------------------------------------ */

    /* --- LED GPIO --- */
    if (!gpio_is_ready_dt(&led)) {
        LOG_ERR("[LED] GPIO not ready - halting");
        return -ENODEV;
    }
    ret = gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE);
    if (ret < 0) {
        LOG_ERR("[LED] configure failed (%d) - halting", ret);
        return ret;
    }
    LOG_INF("[LED] OK");

    /* --- LED thread --- */
    k_thread_create(&led_thread_data, led_stack,
                    K_THREAD_STACK_SIZEOF(led_stack),
                    led_thread, (void *)&led, NULL, NULL,
                    LED_PRIORITY, 0, K_NO_WAIT);
    k_thread_name_set(&led_thread_data, "led");

    /* --- BLE --- */
    ret = ble_init();
    if (ret) {
        LOG_ERR("[BLE] init failed (%d) - halting", ret);
        led_set_error();
        return ret;
    }
    LOG_INF("[BLE] OK - advertising as \"EEG Wearable\"");

    /* --- GPIO1 sanity check on unused pin P1.04 (AIN0, not wired) ---
     * Proves the gpio1 controller works. Do NOT test P1.02/P1.03 here:
     * TWIM22 already owns them via PSEL and reconfiguring them as GPIO
     * corrupts the TWIM pin state (S0D1 drive + pull-up get wiped). */
    {
        const struct device *g1 = DEVICE_DT_GET(DT_NODELABEL(gpio1));
        if (!device_is_ready(g1)) {
            LOG_ERR("[GPIO TEST] gpio1 not ready");
        } else {
            gpio_pin_configure(g1, 4, GPIO_OUTPUT_INACTIVE); /* P1.04 = free */
            LOG_INF("[GPIO TEST] toggling P1.04 3x (scope P1.04 if you can)");
            for (int i = 0; i < 3; i++) {
                gpio_pin_set(g1, 4, 1);
                k_msleep(200);
                gpio_pin_set(g1, 4, 0);
                k_msleep(200);
            }
            gpio_pin_configure(g1, 4, GPIO_DISCONNECTED);
            LOG_INF("[GPIO TEST] P1.04 done - gpio1 works");
        }
    }

    /* --- IMU (LIS2DW12) --- */
    ret = imu_init();
    if (ret == 0) {
        imu_ok = true;
        LOG_INF("[IMU] OK - LIS2DW12 ready, polling @ 25 Hz");
    } else {
        LOG_WRN("[IMU] not available (err %d) - continuing", ret);
    }

    /* --- VBAT ADC --- */
    ret = vbat_init();
    if (ret == 0) {
        vbat_ok = true;
        LOG_INF("[VBAT] OK - P1.13/AIN6, 2M+1M divider, LiPo curve");
    } else {
        LOG_WRN("[VBAT] not available (err %d) - continuing", ret);
    }

    /* --- PMIC status GPIOs --- */
    ret = pmic_init();
    if (ret == 0) {
        pmic_ok = true;
        LOG_INF("[PMIC] OK - CHG=P0.03  ERR=P0.02  (internal pull-ups, active-low)");
    } else {
        LOG_WRN("[PMIC] not available (err %d) - continuing without PMIC status", ret);
    }

    /* --- ADS1299 EEG AFE --- */
    ret = eeg_init();
    if (ret == 0) {
        eeg_ok = true;
        LOG_INF("[EEG] OK - ADS1299 streaming at 250 SPS");
    } else {
        LOG_WRN("[EEG] not available (err %d) - continuing without EEG", ret);
    }

    LOG_INF("Boot complete. IMU=%s  VBAT=%s  EEG=%s  PMIC=%s",
            imu_ok  ? "yes" : "no",
            vbat_ok ? "yes" : "no",
            eeg_ok  ? "yes" : "no",
            pmic_ok ? "yes" : "no");
    LOG_INF("Waiting for BLE connection...");

    /* ------------------------------------------------------------------ */
    /* Main loop - 250 Hz tick (4 ms)                                      */
    /*                                                                     */
    /* - Drains 8-sample batches from the ADS1299 ring buffer and notifies */
    /*   over BLE EEG characteristic (~31 Hz packet rate, 195 bytes each). */
    /* - Samples IMU at 25 Hz and notifies over BLE IMU characteristic.    */
    /* - Logs a short status snapshot every 5 s over RTT.                  */
    /* ------------------------------------------------------------------ */

    struct eeg_sample eeg_batch[EEG_BATCH_SIZE];
    uint8_t  eeg_packet[EEG_PACKET_BYTES];
    uint32_t log_tick = 0;   /* increments every 4 ms, logs every 1250 = 5 s */
    uint32_t imu_tick = 0;   /* increments every 4 ms, samples every 10 = 40 ms */

    while (1) {
        /* EEG — always drain the ring buffer so it never overflows when
         * disconnected. Only pack and notify when actually subscribed. */
        if (eeg_is_ready()) {
            int n = eeg_read(eeg_batch, EEG_BATCH_SIZE);
            if (n == EEG_BATCH_SIZE && ble_eeg_subscribed()) {
                eeg_pack_batch(eeg_batch, eeg_packet);
                ble_notify_eeg(eeg_packet, sizeof(eeg_packet));
            }
        }

        /* IMU sample at 25 Hz */
        if (++imu_tick >= 10) {
            imu_tick = 0;
            if (ble_imu_subscribed() && imu_is_ready()) {
                struct imu_sample s;
                if (imu_read(&s) == 0) {
                    ble_notify_imu(&s);
                }
            }
        }

        /* 5 s status snapshot - one block of RTT output, not continuous */
        if (++log_tick >= 1250) {
            log_tick = 0;

            /* BLE state */
            if (ble_eeg_subscribed()) {
                LOG_INF("[STATUS] BLE: streaming EEG | gains=[%u,%u,%u,%u] SPS=250",
                        eeg_get_group_gain(0), eeg_get_group_gain(1),
                        eeg_get_group_gain(2), eeg_get_group_gain(3));
            } else if (ble_is_connected()) {
                LOG_INF("[STATUS] BLE: connected  | waiting for subscription");
            } else {
                LOG_INF("[STATUS] BLE: advertising");
            }

            /* IMU - one raw sample to show it's alive */
            if (imu_ok && imu_is_ready()) {
                struct imu_sample s;
                if (imu_read(&s) == 0) {
                    LOG_INF("[STATUS] IMU: x=%4d  y=%4d  z=%4d mg  "
                            "temp=%d.%02d C",
                            s.x_mg, s.y_mg, s.z_mg,
                            s.temp_cdeg / 100, s.temp_cdeg % 100);
                }
            }

            /* VBAT - voltage and percentage */
            uint32_t vbat_mv  = 0;
            uint8_t  vbat_pct = 0;
            if (vbat_ok) {
                if (vbat_read_mv(&vbat_mv) == 0 && vbat_percent(&vbat_pct) == 0) {
                    LOG_INF("[STATUS] VBAT: %u mV  %u %%", vbat_mv, vbat_pct);
                }
            }

            /* PMIC - charging and error flags */
            bool chg = pmic_ok && pmic_is_charging();
            bool err = pmic_ok && pmic_is_error();
            if (pmic_ok) {
                LOG_INF("[STATUS] PMIC: %s%s",
                        chg ? "CHARGING " : "",
                        err ? "ERROR"     : (chg ? "" : "idle"));
            }

            /* Push to BLE status characteristic if subscribed */
            if (ble_status_subscribed()) {
                ble_notify_status((uint16_t)vbat_mv, vbat_pct, chg, err);
            }

        }

        k_msleep(4); /* 250 Hz */
    }

    return 0;
}
