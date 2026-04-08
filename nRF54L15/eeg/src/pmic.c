#include "pmic.h"

#include <zephyr/kernel.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(pmic, LOG_LEVEL_INF);

/* DTS aliases resolve to gpio-keys nodes on P0.02 and P0.03.
 * Both are declared GPIO_ACTIVE_LOW | GPIO_PULL_UP in the board DTS, so:
 *   gpio_pin_configure_dt(..., GPIO_INPUT) adds direction; pull/polarity come from dt_flags.
 *   gpio_pin_get_dt(...) returns 1 when the pin is asserted (physically LOW). */
static const struct gpio_dt_spec pmic_err_gpio =
    GPIO_DT_SPEC_GET(DT_ALIAS(pmic_err), gpios);
static const struct gpio_dt_spec pmic_chg_gpio =
    GPIO_DT_SPEC_GET(DT_ALIAS(pmic_chg), gpios);

int pmic_init(void)
{
    if (!gpio_is_ready_dt(&pmic_err_gpio)) {
        LOG_ERR("PMIC_ERR GPIO (P0.02) not ready");
        return -ENODEV;
    }
    if (!gpio_is_ready_dt(&pmic_chg_gpio)) {
        LOG_ERR("PMIC_CHG GPIO (P0.03) not ready");
        return -ENODEV;
    }

    int ret = gpio_pin_configure_dt(&pmic_err_gpio, GPIO_INPUT);
    if (ret) {
        LOG_ERR("PMIC_ERR configure failed (%d)", ret);
        return ret;
    }
    ret = gpio_pin_configure_dt(&pmic_chg_gpio, GPIO_INPUT);
    if (ret) {
        LOG_ERR("PMIC_CHG configure failed (%d)", ret);
        return ret;
    }

    LOG_INF("PMIC GPIO ready  ERR=P0.02  CHG=P0.03  (active-low, pull-up)");
    return 0;
}

bool pmic_is_charging(void)
{
    /* Returns 1 (asserted) when P0.03 is pulled LOW by the nPM1100 charger. */
    return gpio_pin_get_dt(&pmic_chg_gpio) > 0;
}

bool pmic_is_error(void)
{
    /* Returns 1 (asserted) when P0.02 is pulled LOW by the nPM1100 fault logic. */
    return gpio_pin_get_dt(&pmic_err_gpio) > 0;
}
