#include "vbat.h"

#include <zephyr/kernel.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(vbat, LOG_LEVEL_INF);

/* ---------- ADC ---------- */

/* ADC_DT_SPEC_GET requires a consumer node with io-channels pointing to the
 * ADC channel. vbat_sense in DTS has io-channels = <&adc 0>; channel@0 holds
 * gain/reference/pin config. The macro reads both through the phandle. */
static const struct adc_dt_spec vbat_adc = ADC_DT_SPEC_GET(DT_NODELABEL(vbat_sense));

static int16_t        adc_buf;
static struct adc_sequence adc_seq = {
    .buffer      = &adc_buf,
    .buffer_size = sizeof(adc_buf),
};

/* ---------- Resistor divider ---------- */

/* R_top = 2 MΩ, R_bot = 1 MΩ → ratio = 1/3.
 * V_bat = V_adc × (R_top + R_bot) / R_bot = V_adc × 3. */
#define DIVIDER_FULL_OHMS   3000000UL
#define DIVIDER_OUT_OHMS    1000000UL

/* ---------- LiPo discharge curve ----------
 *
 * Lookup table: battery voltage (mV) → state of charge (%).
 * Based on the typical single-cell LiPo discharge curve.
 * Linear interpolation is used between points.
 *
 * 4200 mV = fully charged (charger cutoff)
 * 3000 mV = empty (protect circuit cutoff for small cells)
 */
static const struct {
    uint32_t mv;
    uint8_t  pct;
} lipo_curve[] = {
    { 4200, 100 },
    { 4150,  97 },
    { 4110,  94 },
    { 4080,  91 },
    { 4020,  85 },
    { 3980,  79 },
    { 3950,  73 },
    { 3910,  65 },
    { 3870,  57 },
    { 3830,  50 },
    { 3790,  42 },
    { 3750,  35 },
    { 3710,  27 },
    { 3670,  20 },
    { 3610,  13 },
    { 3490,   6 },
    { 3300,   2 },
    { 3000,   0 },
};

static uint8_t mv_to_percent(uint32_t mv)
{
    if (mv >= lipo_curve[0].mv) {
        return 100;
    }
    for (size_t i = 1; i < ARRAY_SIZE(lipo_curve); i++) {
        if (mv >= lipo_curve[i].mv) {
            /* Linear interpolation between entry i-1 and i. */
            uint32_t v_hi = lipo_curve[i - 1].mv;
            uint32_t v_lo = lipo_curve[i].mv;
            uint8_t  p_hi = lipo_curve[i - 1].pct;
            uint8_t  p_lo = lipo_curve[i].pct;
            return p_lo + (uint8_t)((mv - v_lo) * (p_hi - p_lo) / (v_hi - v_lo));
        }
    }
    return 0;
}

/* ---------- Public API ---------- */

int vbat_init(void)
{
    if (!adc_is_ready_dt(&vbat_adc)) {
        LOG_ERR("VBAT ADC device not ready");
        return -ENODEV;
    }
    int ret = adc_channel_setup_dt(&vbat_adc);
    if (ret) {
        LOG_ERR("ADC channel setup failed (err %d)", ret);
        return ret;
    }
    ret = adc_sequence_init_dt(&vbat_adc, &adc_seq);
    if (ret) {
        LOG_ERR("ADC sequence init failed (err %d)", ret);
        return ret;
    }
    LOG_INF("VBAT ADC ready (P1.13 = AIN6, gain=1/4, ref=0.6 V internal)");
    return 0;
}

int vbat_read_mv(uint32_t *mv_out)
{
    int ret = adc_read(vbat_adc.dev, &adc_seq); // Triggers the ADC to sample the voltage on the pin and stores into adc buffer
    if (ret) {
        return ret;
    }

    /* adc_raw_to_millivolts_dt converts the raw count to the voltage
     * seen at the ADC pin using the gain and reference from the DT spec. */
    int32_t val_mv = adc_buf;
    ret = adc_raw_to_millivolts_dt(&vbat_adc, &val_mv);
    if (ret) {
        return ret;
    }

    /* Scale back up through the resistor divider. */
    *mv_out = (uint32_t)val_mv * DIVIDER_FULL_OHMS / DIVIDER_OUT_OHMS;
    return 0;
}

int vbat_percent(uint8_t *pct_out)
{
    uint32_t mv;
    int ret = vbat_read_mv(&mv);
    if (ret) {
        return ret;
    }
    *pct_out = mv_to_percent(mv);
    return 0;
}