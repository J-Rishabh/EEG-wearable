#include <zephyr/kernel.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/logging/log.h>
#include "ble.h"

LOG_MODULE_REGISTER(main, LOG_LEVEL_INF);

#define LED0_NODE DT_ALIAS(led0)
static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(LED0_NODE, gpios);

/* ---------- LED state ---------- */

static ATOMIC_DEFINE(led_error_flag, 1); /* set → fast blink error pattern */

static void led_set_error(void)
{
    atomic_set_bit(led_error_flag, 0);
}

/* ---------- LED thread ----------
 *
 * Priority order (highest → lowest):
 *   Error       → fast blink 100 ms on / 100 ms off (sticky, never clears)
 *   Connected   → solid on
 *   Advertising → breathing: triangle-wave duty 0→100→0 % over ~3 s
 */

#define LED_STACK_SIZE   512
#define LED_PRIORITY     7

#define BREATH_STEPS     20
#define BREATH_STEP_MS   75   /* 20 steps × 75 ms × 2 halves ≈ 3 s per breath */

K_THREAD_STACK_DEFINE(led_stack, LED_STACK_SIZE);
static struct k_thread led_thread_data;

static void led_thread(void *p1, void *p2, void *p3)
{
    ARG_UNUSED(p2);
    ARG_UNUSED(p3);

    const struct gpio_dt_spec *l = p1;
    int step = 0;
    bool rising = true;

    LOG_INF("LED thread started");

    while (1) {
        if (atomic_test_bit(led_error_flag, 0)) {
            /* Error: fast blink — stays here forever. */
            gpio_pin_set_dt(l, 1);
            k_msleep(100);
            gpio_pin_set_dt(l, 0);
            k_msleep(100);
        } else if (ble_is_connected()) {
            /* Connected: solid on. */
            gpio_pin_set_dt(l, 1);
            k_msleep(50);
        } else {
            /* Advertising: breathing software PWM.
             * step=0  → 0 % duty (off), step=STEPS → 100 % duty (on). */
            int on_ms  = (step * BREATH_STEP_MS) / BREATH_STEPS;
            int off_ms = BREATH_STEP_MS - on_ms;

            if (on_ms > 0) {
                gpio_pin_set_dt(l, 1);
                k_msleep(on_ms);
            }
            if (off_ms > 0) {
                gpio_pin_set_dt(l, 0);
                k_msleep(off_ms);
            }

            /* Advance triangle wave. */
            if (rising) {
                if (++step >= BREATH_STEPS) {
                    rising = false;
                }
            } else {
                if (--step <= 0) {
                    rising = true;
                }
            }
        }
    }
}

/* ---------- Main ---------- */

int main(void)
{
    int ret;

    LOG_INF("Booting EEG Wearable");

    /* --- GPIO check --- */
    if (!gpio_is_ready_dt(&led)) {
        LOG_ERR("LED GPIO not ready — halting");
        return -ENODEV;
    }
    LOG_INF("LED GPIO ready");

    ret = gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE);
    if (ret < 0) {
        LOG_ERR("LED GPIO configure failed (err %d) — halting", ret);
        return ret;
    }
    LOG_INF("LED GPIO configured");

    /* --- Start LED thread --- */
    k_thread_create(&led_thread_data, led_stack, K_THREAD_STACK_SIZEOF(led_stack),
                    led_thread, (void *)&led, NULL, NULL,
                    LED_PRIORITY, 0, K_NO_WAIT);
    k_thread_name_set(&led_thread_data, "led");
    LOG_INF("LED thread created");

    /* --- BLE init --- */
    ret = ble_init();
    if (ret) {
        LOG_ERR("BLE init failed (err %d)", ret);
        led_set_error();
        return ret;
    }
    LOG_INF("BLE init OK — advertising");

    /* --- Main loop ---
     * Sends an incrementing 4-byte counter at ~250 Hz to verify the BLE
     * pipeline before the ADS1299 driver is wired in.
     * In nRF Connect: connect → find EEG Service → subscribe to EEG Data.
     * State is logged every 5 s so you can follow along in RTT. */
    uint32_t counter = 0;
    uint8_t  buf[4];
    uint32_t log_tick = 0;  /* increments every 4 ms, log every 1250 = 5 s */

    while (1) {
        if (ble_eeg_subscribed()) {
            sys_put_be32(counter++, buf);
            ble_notify_eeg(buf, sizeof(buf));
        }

        /* Periodic state log so RTT always shows something useful. */
        if (++log_tick >= 1250) {
            log_tick = 0;
            if (ble_eeg_subscribed()) {
                LOG_INF("State: streaming  counter=%u", counter);
            } else if (ble_is_connected()) {
                LOG_INF("State: connected, waiting for subscription");
            } else {
                LOG_INF("State: advertising");
            }
        }

        k_msleep(4); /* 250 Hz */
    }

    return 0;
}
