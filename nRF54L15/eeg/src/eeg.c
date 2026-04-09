#include "eeg.h"

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/ring_buffer.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(eeg, LOG_LEVEL_INF);

/* ---------- Device tree handles ---------- */

#define ADS_NODE DT_NODELABEL(ads1299)

/* SPI spec — Mode 1 (CPOL=0, CPHA=1): ADS1299 clocks data on falling SCLK edge.
 * SPI_MODE_CPHA = CPHA=1, no CPOL flag = CPOL=0. */
static const struct spi_dt_spec ads_spi = SPI_DT_SPEC_GET(
    ADS_NODE,
    SPI_OP_MODE_MASTER | SPI_TRANSFER_MSB | SPI_WORD_SET(8) | SPI_MODE_CPHA,
    0);

/* Control signal GPIO specs — polarity (ACTIVE_HIGH/LOW) is encoded in the DT.
 * Use gpio_pin_set_dt(spec, 1) = logical "asserted", 0 = logical "deasserted". */
static const struct gpio_dt_spec drdy_gpio  = GPIO_DT_SPEC_GET(ADS_NODE, drdy_gpios);
static const struct gpio_dt_spec start_gpio = GPIO_DT_SPEC_GET(ADS_NODE, start_gpios);
static const struct gpio_dt_spec pwdn_gpio  = GPIO_DT_SPEC_GET(ADS_NODE, pwdn_gpios);
static const struct gpio_dt_spec reset_gpio = GPIO_DT_SPEC_GET(ADS_NODE, reset_gpios);

/* ---------- Register addresses ---------- */

#define REG_ID          0x00
#define REG_CONFIG1     0x01
#define REG_CONFIG2     0x02
#define REG_CONFIG3     0x03
#define REG_LOFF        0x04
#define REG_CH1SET      0x05  /* CH1–CH8SET are contiguous: 0x05–0x0C */
#define REG_CH2SET      0x06
#define REG_CH3SET      0x07
#define REG_CH4SET      0x08
#define REG_CH5SET      0x09
#define REG_CH6SET      0x0A
#define REG_CH7SET      0x0B
#define REG_CH8SET      0x0C
#define REG_BIAS_SENSP  0x0D
#define REG_BIAS_SENSN  0x0E
#define REG_MISC1       0x15
#define REG_CONFIG4     0x17

/* ---------- SPI command opcodes ---------- */

#define CMD_SDATAC   0x11  /* stop  data continuous mode */
#define CMD_RDATAC   0x10  /* start data continuous mode */
#define CMD_START    0x08  /* begin conversions (also controllable via START pin) */
#define CMD_RREG     0x20  /* | register_addr, then count-1, then read */
#define CMD_WREG     0x40  /* | register_addr, then count-1, then data */

/* ---------- Register configuration values ----------
 *
 * CONFIG1 = 0x96
 *   [7]   = 1   reserved, must be 1
 *   [6]   = 0   CLK_EN off (no clock output on CLK pin)
 *   [5]   = 0   reserved
 *   [4]   = 1   reserved, must be 1
 *   [3:0] = DR — bits [2:0] = 110 = 250 SPS
 *   Reset value is 0x96 — we write it explicitly to be safe.
 */
#define CFG1_250SPS  0x96

/* CONFIG2 = 0xC0
 *   Leave at reset (internal test signals disabled, no test square wave).
 */
#define CFG2_RESET   0xC0

/* CONFIG3 = 0xF8
 *   [7] PD_REFBUF   = 1  internal 4.5 V reference buffer powered up
 *   [6] reserved    = 1  must be 1
 *   [5] BIAS_MEAS   = 1  route BIAS_IN to CH8 ADC input for monitoring
 *   [4] BIASREF_INT = 1  BIAS reference = internal AVDD/2 (BIASREF pin unused)
 *   [3] PD_BIAS     = 1  BIAS amplifier powered up → drives BIASOUT/DRL
 *   [2] BIAS_LOFF_SENS = 0  lead-off sense disabled (active electrodes, not needed)
 *   [1] BIAS_STAT   = 0  read-only
 *   [0] reserved    = 0
 *   = 1111 1000 = 0xF8
 */
#define CFG3_ACTIVE  0xF8

/* CHnSET for active EEG channels (CH1–CH6):
 *   [7]   PD   = 0    channel powered up
 *   [6:4] GAIN = 110  × 24 gain
 *   [3]   SRB2 = 0    INP not connected to SRB2
 *   [2:0] MUX  = 000  normal electrode input
 *   = 0110 0000 = 0x60
 */
#define CHNSET_EEG   0x60

/* CHnSET for CH7 — IN7P/N tied to AVDD on schematic, no electrode present.
 * Power it down to prevent AVDD feeding into the ADC.
 *   [7]   PD   = 1    channel powered down
 *   [6:4] GAIN = 000  (irrelevant when powered down)
 *   [2:0] MUX  = 001  input shorted (clean idle state when/if re-enabled)
 *   = 1000 0001 = 0x81
 */
#define CHNSET_OFF   0x81

/* CHnSET for CH8 — BIAS_MEAS monitoring channel.
 *   BIAS_MEAS=1 in CONFIG3 internally routes the BIAS amplifier input (BIAS_IN,
 *   the averaged common-mode of CH1–CH6) to CH8's ADC input.
 *   The external IN8P/N tie to AVDD does not affect this internal routing.
 *   GAIN=1 is appropriate: the bias signal is a large (mV-range) common-mode.
 *   [7]   PD   = 0    channel powered up (needed for BIAS_MEAS to work)
 *   [6:4] GAIN = 000  × 1 gain
 *   [3]   SRB2 = 0
 *   [2:0] MUX  = 000  normal (BIAS_MEAS overrides via internal routing)
 *   = 0000 0000 = 0x00
 */
#define CHNSET_BIASMEAS  0x00

/* BIAS_SENSP / BIAS_SENSN = 0x3F
 *   Bits [5:0] = CH1–CH6 contribute to the BIAS amplifier's common-mode input.
 *   CH7 and CH8 (bits 6–7) excluded: CH7 is off, CH8 is the BIAS_MEAS channel.
 */
#define BIAS_SENS_CH1_6  0x3F

/* MISC1 = 0x20
 *   [5] SRB1 = 1  connects SRB1 pin to INM of every channel (referential montage).
 *               The reference electrode is wired to SRB1; all channels measure
 *               electrode_voltage − reference.
 */
#define MISC1_SRB1  0x20

/* ADS1299 device ID — 8-channel variant */
#define ADS1299_ID_EXPECTED  0x3E

/* ---------- State ---------- */

static bool    ads_ready;
static bool    test_mode_on;
/* Per-group PGA gains — indexed by group 0–3 (see eeg.h for mapping). */
static uint8_t current_gain[EEG_NUM_GAIN_GROUPS] = {
    EEG_GAIN_DEFAULT,   /* group 0: CH1        EOG      */
    EEG_GAIN_DEFAULT,   /* group 1: CH2, CH3   EMG/ECG  */
    EEG_GAIN_DEFAULT,   /* group 2: CH4, CH6   EEG L1/3 */
    EEG_GAIN_DEFAULT,   /* group 3: CH5        EEG L2   */
};

/* DRDY GPIO interrupt bookkeeping */
static struct gpio_callback drdy_cb;

/* Work item: ISR submits this; system workqueue runs the SPI read. */
static struct k_work eeg_work;

/* Ring buffer — 32 samples × sizeof(struct eeg_sample) bytes.
 * At 250 SPS that's 128 ms of headroom before the main loop must drain.
 * Always written in complete eeg_sample-sized chunks so alignment is preserved. */
#define EEG_RING_SAMPLES  32
#define EEG_RING_BYTES    (EEG_RING_SAMPLES * sizeof(struct eeg_sample))

RING_BUF_DECLARE(eeg_ring, EEG_RING_BYTES);

/* Running batch index embedded in every BLE packet header.
 * Increments by EEG_BATCH_SIZE each pack call; Python side detects gaps. */
static uint16_t eeg_batch_idx;

/* ---------- Low-level SPI helpers ---------- */

/* Send a single-byte opcode (SDATAC, RDATAC, START, STOP …) */
static int ads_cmd(uint8_t opcode)
{
    struct spi_buf     tx     = { .buf = &opcode, .len = 1 };
    struct spi_buf_set tx_set = { .buffers = &tx,  .count = 1 };
    return spi_transceive_dt(&ads_spi, &tx_set, NULL);
}

/* Write one register.
 * WREG format: [0x40|addr], [count-1 = 0x00], [data byte] */
static int ads_wreg(uint8_t addr, uint8_t val)
{
    uint8_t buf[3] = { CMD_WREG | (addr & 0x1F), 0x00, val };
    struct spi_buf     tx     = { .buf = buf, .len = 3 };
    struct spi_buf_set tx_set = { .buffers = &tx, .count = 1 };
    return spi_transceive_dt(&ads_spi, &tx_set, NULL);
}

/* Read one register and return its value, or negative errno on failure.
 * RREG format: send [0x20|addr], [count-1 = 0x00], [dummy]; receive 3 bytes.
 * The register data comes back in the third received byte. */
static int ads_rreg(uint8_t addr)
{
    uint8_t tx_buf[3] = { CMD_RREG | (addr & 0x1F), 0x00, 0x00 };
    uint8_t rx_buf[3] = { 0 };
    struct spi_buf     tx     = { .buf = tx_buf, .len = 3 };
    struct spi_buf     rx     = { .buf = rx_buf, .len = 3 };
    struct spi_buf_set tx_set = { .buffers = &tx, .count = 1 };
    struct spi_buf_set rx_set = { .buffers = &rx, .count = 1 };
    int ret = spi_transceive_dt(&ads_spi, &tx_set, &rx_set);
    return (ret < 0) ? ret : (int)rx_buf[2];
}

/* Read a 27-byte conversion frame while in RDATAC mode.
 * In RDATAC the ADS1299 clocks out a new frame on every DRDY pulse.
 * We simply provide 27 dummy TX bytes and capture 27 RX bytes. */
static int ads_read_frame(uint8_t frame[27])
{
    static uint8_t tx_dummy[27]; /* zero-initialised by BSS */
    struct spi_buf     tx     = { .buf = tx_dummy, .len = 27 };
    struct spi_buf     rx     = { .buf = frame,    .len = 27 };
    struct spi_buf_set tx_set = { .buffers = &tx, .count = 1 };
    struct spi_buf_set rx_set = { .buffers = &rx, .count = 1 };
    return spi_transceive_dt(&ads_spi, &tx_set, &rx_set);
}

/* ---------- Frame parsing ----------
 *
 * ADS1299 output frame layout (27 bytes, MSB first):
 *   Bytes  0–2:  Status word [23:0]
 *   Bytes  3–5:  CH1 [23:0]
 *   Bytes  6–8:  CH2 [23:0]
 *   ...
 *   Bytes 24–26: CH8 [23:0]
 *
 * Each channel value is a 24-bit 2's-complement integer.
 * Sign-extension to 32 bits: shift up to fill the MSByte, then arithmetic
 * right-shift back — this propagates the sign bit without undefined behaviour
 * (the cast to int32_t before the shift makes it well-defined in C99/C11). */
static struct eeg_sample parse_frame(const uint8_t *frame)
{
    struct eeg_sample s;

    s.status = ((uint32_t)frame[0] << 16) |
               ((uint32_t)frame[1] <<  8) |
                (uint32_t)frame[2];

    for (int i = 0; i < EEG_NUM_CHANNELS; i++) {
        const uint8_t *p = &frame[3 + i * 3];
        s.ch[i] = (int32_t)((uint32_t)p[0] << 24 |
                             (uint32_t)p[1] << 16 |
                             (uint32_t)p[2] <<  8) >> 8;
    }

    return s;
}

/* ---------- DRDY interrupt + workqueue ---------- */

/* System workqueue handler.
 * Runs after the ISR submits eeg_work.  Safe to call SPI here.
 * Do NOT call directly — always submitted via k_work_submit. */
static void eeg_work_handler(struct k_work *work)
{
    ARG_UNUSED(work);

    if (!ads_ready) {
        return;
    }

    uint8_t frame[27];
    int ret = ads_read_frame(frame);
    if (ret < 0) {
        LOG_ERR("SPI frame read failed (%d)", ret);
        return;
    }

    struct eeg_sample s = parse_frame(frame);

    /* Only write if there is space for a complete sample — avoids partial
     * writes that would break the size alignment of the ring buffer. */
    if (ring_buf_space_get(&eeg_ring) >= sizeof(s)) {
        ring_buf_put(&eeg_ring, (uint8_t *)&s, sizeof(s));
    } else {
        /* Ring buffer full: drop this sample.
         * The main loop is not draining fast enough.
         * Log every 64 drops to avoid spamming RTT. */
        static uint32_t drop_cnt;
        if ((++drop_cnt & 0x3F) == 1) {
            LOG_WRN("EEG ring buffer full — %u samples dropped", drop_cnt);
        }
    }
}

/* DRDY ISR — falling edge signals a completed conversion.
 * Must not do SPI here (interrupt context) — just wake the workqueue. */
static void drdy_isr(const struct device *port, struct gpio_callback *cb,
                     uint32_t pins)
{
    ARG_UNUSED(port);
    ARG_UNUSED(cb);
    ARG_UNUSED(pins);
    k_work_submit(&eeg_work);
}

/* ---------- Public API ---------- */

int eeg_init(void)
{
    int ret;

    /* --- Device readiness checks --- */
    if (!spi_is_ready_dt(&ads_spi)) {
        LOG_ERR("ADS1299 SPI bus not ready");
        return -ENODEV;
    }
    if (!gpio_is_ready_dt(&drdy_gpio)  ||
        !gpio_is_ready_dt(&start_gpio) ||
        !gpio_is_ready_dt(&pwdn_gpio)  ||
        !gpio_is_ready_dt(&reset_gpio)) {
        LOG_ERR("ADS1299 control GPIO not ready");
        return -ENODEV;
    }

    /* --- Configure GPIO directions ---
     *
     * START (active-high):  OUTPUT_INACTIVE → LOW → conversions not started yet
     * PWDN  (active-low):   OUTPUT_ACTIVE   → LOW → device powered down until step 1
     * RESET (active-low):   OUTPUT_INACTIVE → HIGH → not in reset (will pulse below)
     * DRDY  (active-low):   INPUT only, interrupt configured later
     */
    ret = gpio_pin_configure_dt(&start_gpio, GPIO_OUTPUT_INACTIVE);
    if (ret) { LOG_ERR("START pin config failed (%d)", ret); return ret; }

    ret = gpio_pin_configure_dt(&pwdn_gpio, GPIO_OUTPUT_ACTIVE);
    if (ret) { LOG_ERR("PWDN pin config failed (%d)",  ret); return ret; }

    ret = gpio_pin_configure_dt(&reset_gpio, GPIO_OUTPUT_INACTIVE);
    if (ret) { LOG_ERR("RESET pin config failed (%d)", ret); return ret; }

    ret = gpio_pin_configure_dt(&drdy_gpio, GPIO_INPUT);
    if (ret) { LOG_ERR("DRDY pin config failed (%d)",  ret); return ret; }

    /* --- Step 1: Power on ---
     * Deassert PWDN (active-low): gpio_pin_set_dt(…, 0) = logical low = pin HIGH.
     * For GPIO_ACTIVE_LOW, value=0 → pin HIGH → device powered on. */
    gpio_pin_set_dt(&pwdn_gpio, 0);
    k_msleep(1);    /* tPOR: wait for power-on reset to complete (~18 CLKIN cycles) */

    /* --- Step 2: RESET pulse ---
     * Drive RESET low (assert) for ≥ 2 CLKIN cycles (≥ ~1 µs at 2.048 MHz).
     * Using 10 µs to be conservative.
     * For GPIO_ACTIVE_LOW: value=1 → pin LOW (reset asserted).
     *                      value=0 → pin HIGH (reset deasserted). */
    gpio_pin_set_dt(&reset_gpio, 1);    /* assert:   RESET = LOW */
    k_busy_wait(10);
    gpio_pin_set_dt(&reset_gpio, 0);    /* deassert: RESET = HIGH */
    k_busy_wait(18);                    /* 9 CLKIN cycles settle before SPI */

    /* --- Step 3: Stop any leftover RDATAC from a prior session ---
     * SDATAC must be the first command after reset; it is safe to issue even
     * if the device was not in RDATAC. */
    ret = ads_cmd(CMD_SDATAC);
    if (ret) { LOG_ERR("SDATAC failed (%d)", ret); return ret; }
    k_busy_wait(5);

    /* --- Step 4: Verify device ID ---
     * Confirms SPI wiring + polarity are correct before touching any registers. */
    int id = ads_rreg(REG_ID);
    if (id < 0) {
        LOG_ERR("ID register read failed (%d)", id);
        return id;
    }
    if ((uint8_t)id != ADS1299_ID_EXPECTED) {
        LOG_ERR("ADS1299 ID mismatch: got 0x%02X, expected 0x%02X",
                (uint8_t)id, ADS1299_ID_EXPECTED);
        return -EIO;
    }
    LOG_INF("ADS1299 ID OK (0x%02X)", (uint8_t)id);

    /* --- Step 5: CONFIG3 — reference + bias block ---
     * 0xF8 = PD_REFBUF=1, reserved=1, BIAS_MEAS=1, BIASREF_INT=1, PD_BIAS=1,
     *         BIAS_LOFF_SENS=0.
     * After writing, wait 150 µs for the internal reference buffer to settle. */
    ret = ads_wreg(REG_CONFIG3, CFG3_ACTIVE);
    if (ret) { LOG_ERR("CONFIG3 write failed (%d)", ret); return ret; }
    k_busy_wait(150);

    /* --- Step 6: CONFIG1 — 250 SPS data rate ---
     * Reset value is already 0x96 (250 SPS); written explicitly for clarity. */
    ret = ads_wreg(REG_CONFIG1, CFG1_250SPS);
    if (ret) { LOG_ERR("CONFIG1 write failed (%d)", ret); return ret; }

    /* --- Step 7: CONFIG2 — leave at reset (0xC0), no test signals --- */
    ret = ads_wreg(REG_CONFIG2, CFG2_RESET);
    if (ret) { LOG_ERR("CONFIG2 write failed (%d)", ret); return ret; }

    /* --- Step 8: Channel configuration ---
     *
     * CH1–CH6  CHNSET_EEG  (0x60): GAIN=24, MUX=electrode, powered up
     * CH7      CHNSET_OFF  (0x81): powered down — IN7P/N tied to AVDD on schematic
     * CH8      CHNSET_BIASMEAS (0x00): GAIN=1, MUX=000, powered up for BIAS_MEAS
     */
    for (uint8_t reg = REG_CH1SET; reg <= REG_CH6SET; reg++) {
        ret = ads_wreg(reg, CHNSET_EEG);
        if (ret) {
            LOG_ERR("CH%uSET write failed (%d)", reg - REG_CH1SET + 1, ret);
            return ret;
        }
    }
    ret = ads_wreg(REG_CH7SET, CHNSET_OFF);
    if (ret) { LOG_ERR("CH7SET write failed (%d)", ret); return ret; }

    ret = ads_wreg(REG_CH8SET, CHNSET_BIASMEAS);
    if (ret) { LOG_ERR("CH8SET write failed (%d)", ret); return ret; }

    /* --- Step 9: BIAS sense inputs ---
     * CH1–CH6 feed the BIAS amplifier's common-mode averaging circuit.
     * The amplifier output drives BIASOUT (DRL electrode via protection resistor). */
    ret = ads_wreg(REG_BIAS_SENSP, BIAS_SENS_CH1_6);
    if (ret) { LOG_ERR("BIAS_SENSP write failed (%d)", ret); return ret; }

    ret = ads_wreg(REG_BIAS_SENSN, BIAS_SENS_CH1_6);
    if (ret) { LOG_ERR("BIAS_SENSN write failed (%d)", ret); return ret; }

    /* --- Step 10: MISC1 — referential montage ---
     * SRB1=1 connects the SRB1 pin (reference electrode) to the inverting
     * input of all active channels — all measurements are vs the reference. */
    ret = ads_wreg(REG_MISC1, MISC1_SRB1);
    if (ret) { LOG_ERR("MISC1 write failed (%d)", ret); return ret; }

    /* --- Step 11: DRDY interrupt ---
     * DRDY is active-low; GPIO_INT_EDGE_TO_ACTIVE fires on the falling edge.
     * The ISR only submits a work item — no SPI in interrupt context. */
    k_work_init(&eeg_work, eeg_work_handler);

    gpio_init_callback(&drdy_cb, drdy_isr, BIT(drdy_gpio.pin));
    ret = gpio_add_callback(drdy_gpio.port, &drdy_cb);
    if (ret) { LOG_ERR("DRDY callback add failed (%d)", ret); return ret; }

    ret = gpio_pin_interrupt_configure_dt(&drdy_gpio, GPIO_INT_EDGE_TO_ACTIVE);
    if (ret) { LOG_ERR("DRDY interrupt configure failed (%d)", ret); return ret; }

    /* --- Step 12: Begin continuous conversions ---
     * Assert START (active-high) then enter RDATAC.
     * After RDATAC, the ADS1299 sends a frame on every DRDY pulse; the ISR
     * picks these up and the workqueue handler reads them via SPI. */
    gpio_pin_set_dt(&start_gpio, 1);

    ret = ads_cmd(CMD_RDATAC);
    if (ret) { LOG_ERR("RDATAC failed (%d)", ret); return ret; }

    ads_ready = true;
    LOG_INF("ADS1299 ready: gains=[%d,%d,%d,%d] CH7 off, CH8 BIAS_MEAS, "
            "250 SPS, SRB1 referential, DRL active",
            EEG_GAIN_DEFAULT, EEG_GAIN_DEFAULT,
            EEG_GAIN_DEFAULT, EEG_GAIN_DEFAULT);
    return 0;
}

bool eeg_is_ready(void)
{
    return ads_ready;
}

int32_t eeg_raw_to_uv(int32_t raw)
{
    /* Uses group-0 gain as representative. For per-channel conversion,
     * use the gain returned by eeg_get_group_gain() for the relevant group. */
    return (int32_t)((int64_t)raw * 4500000LL / current_gain[0] / 8388608LL);
}

int eeg_read(struct eeg_sample *out, int count)
{
    uint32_t want = (uint32_t)count * sizeof(struct eeg_sample);
    /* Only read if a full batch is available — ring_buf_get() consumes whatever
     * bytes it returns, so a partial read would silently discard samples.
     * The main loop runs at ~222 Hz (k_msleep(4) rounds up to ~4.5 ms), while
     * DRDY fires at 250 Hz, so without this guard the ring is nearly always
     * drained 1 sample at a time before 8 accumulate → almost no notifications. */
    if (ring_buf_size_get(&eeg_ring) < want) {
        return 0;
    }
    uint32_t got = ring_buf_get(&eeg_ring, (uint8_t *)out, want);
    return (int)(got / sizeof(struct eeg_sample));
}

void eeg_pack_batch(const struct eeg_sample batch[EEG_BATCH_SIZE],
                    uint8_t packet[EEG_PACKET_BYTES])
{
    /* Header: 2B index + 4B gains (one per group) */
    sys_put_le16(eeg_batch_idx, &packet[0]);
    packet[2] = current_gain[0];
    packet[3] = current_gain[1];
    packet[4] = current_gain[2];
    packet[5] = current_gain[3];
    eeg_batch_idx += EEG_BATCH_SIZE;

    /* Payload: 8 samples × 8 channels × 3 bytes big-endian int24 */
    uint8_t *p = &packet[6];
    for (int s = 0; s < EEG_BATCH_SIZE; s++) {
        for (int ch = 0; ch < EEG_NUM_CHANNELS; ch++) {
            int32_t v = batch[s].ch[ch];
            p[0] = (uint8_t)((v >> 16) & 0xFF);
            p[1] = (uint8_t)((v >>  8) & 0xFF);
            p[2] = (uint8_t)( v        & 0xFF);
            p   += 3;
        }
    }
}

int eeg_set_group_gain(uint8_t group, uint8_t gain)
{
    /* Channel registers for each group (0-indexed from REG_CH1SET). */
    static const uint8_t group_regs[EEG_NUM_GAIN_GROUPS][2] = {
        { REG_CH1SET, 0xFF },           /* group 0: CH1 only */
        { REG_CH2SET, REG_CH3SET },     /* group 1: CH2, CH3 */
        { REG_CH4SET, REG_CH6SET },     /* group 2: CH4, CH6 */
        { REG_CH5SET, 0xFF },           /* group 3: CH5 only */
    };

    static const struct { uint8_t gain; uint8_t chnset; } gain_table[] = {
        { 1,  0x00 }, { 2,  0x10 }, { 4,  0x20 }, { 6,  0x30 },
        { 8,  0x40 }, { 12, 0x50 }, { 24, 0x60 },
    };

    if (group >= EEG_NUM_GAIN_GROUPS) {
        LOG_ERR("Invalid gain group %u — valid: 0–3", group);
        return -EINVAL;
    }

    uint8_t chnset = 0xFF;
    for (int i = 0; i < (int)ARRAY_SIZE(gain_table); i++) {
        if (gain_table[i].gain == gain) {
            chnset = gain_table[i].chnset;
            break;
        }
    }
    if (chnset == 0xFF) {
        LOG_ERR("Invalid gain %u — valid: 1 2 4 6 8 12 24", gain);
        return -EINVAL;
    }

    ads_cmd(CMD_SDATAC);
    for (int i = 0; i < 2; i++) {
        uint8_t reg = group_regs[group][i];
        if (reg != 0xFF) {
            ads_wreg(reg, chnset);
        }
    }
    ads_cmd(CMD_RDATAC);

    current_gain[group] = gain;
    LOG_INF("ADS1299 group %u gain → %u (CHnSET=0x%02X)", group, gain, chnset);
    return 0;
}

uint8_t eeg_get_group_gain(uint8_t group)
{
    return (group < EEG_NUM_GAIN_GROUPS) ? current_gain[group] : 0;
}

/* ---------- Internal test mode ----------
 *
 * Channel-to-group mapping for CH1–CH6 (0-indexed register offsets from REG_CH1SET):
 *   CH1(0)→group 0, CH2(1)→group 1, CH3(2)→group 1,
 *   CH4(3)→group 2, CH5(4)→group 3, CH6(5)→group 2
 *
 * CHnSET in test mode: same GAIN[2:0] bits as normal, MUX changed to 101
 * (internal calibration signal) instead of 000 (normal electrode).
 * Bit mask: (existing & 0xF8) | 0x05.
 *
 * CONFIG2 = 0xD0:
 *   [7,6] = 1,1  reserved, must be 1
 *   [4]   = 1    INT_CAL — generate test signal internally (not from a pin)
 *   [0]   = 0    CAL_FREQ — f_CLK/2^21 ≈ 1 Hz square wave at 2.048 MHz clock
 *   [2:1] = 0,0  CAL_AMP  — ±(VREFP−VREFN)/2400 = ±1.875 mV (well within range
 *                           at any gain setting, no saturation)
 */
#define CFG2_TEST_INT  0xD0

void eeg_set_test_mode(bool enable)
{
    if (!ads_ready) {
        LOG_WRN("Test mode request ignored — ADS1299 not ready");
        return;
    }

    static const uint8_t ch_group[6] = {0, 1, 1, 2, 3, 2};

    ads_cmd(CMD_SDATAC);

    if (enable) {
        ads_wreg(REG_CONFIG2, CFG2_TEST_INT);
        for (int ch = 0; ch < 6; ch++) {
            uint8_t g    = current_gain[ch_group[ch]];
            /* re-derive the GAIN bits by iterating the same table used in
             * eeg_set_group_gain — keeps the two tables in sync */
            static const struct { uint8_t gain; uint8_t bits; } tbl[] = {
                {1,0x00},{2,0x10},{4,0x20},{6,0x30},{8,0x40},{12,0x50},{24,0x60},
            };
            uint8_t gain_bits = 0x60; /* fallback: ×24 */
            for (int i = 0; i < (int)ARRAY_SIZE(tbl); i++) {
                if (tbl[i].gain == g) { gain_bits = tbl[i].bits; break; }
            }
            ads_wreg(REG_CH1SET + ch, gain_bits | 0x05); /* MUX=101 */
        }
    } else {
        ads_wreg(REG_CONFIG2, CFG2_RESET);
        for (int ch = 0; ch < 6; ch++) {
            uint8_t g    = current_gain[ch_group[ch]];
            static const struct { uint8_t gain; uint8_t bits; } tbl[] = {
                {1,0x00},{2,0x10},{4,0x20},{6,0x30},{8,0x40},{12,0x50},{24,0x60},
            };
            uint8_t gain_bits = 0x60;
            for (int i = 0; i < (int)ARRAY_SIZE(tbl); i++) {
                if (tbl[i].gain == g) { gain_bits = tbl[i].bits; break; }
            }
            ads_wreg(REG_CH1SET + ch, gain_bits); /* MUX=000 — normal electrode */
        }
    }

    ads_cmd(CMD_RDATAC);
    test_mode_on = enable;
    LOG_INF("ADS1299 test mode %s", enable ? "enabled (~1 Hz square wave, ±1.875 mV)"
                                           : "disabled (normal electrode input)");
}

bool eeg_test_mode_active(void)
{
    return test_mode_on;
}

static bool drl_on = true;  /* matches init state — DRL active after eeg_init() */

void eeg_set_drl(bool enable)
{
    if (!ads_ready) {
        LOG_WRN("DRL write ignored — ADS1299 not ready");
        return;
    }

    ads_cmd(CMD_SDATAC);
    /* Setting BIAS_SENSP/SENSN to 0x00 disconnects all channels from the BIAS
     * amplifier input.  The amplifier itself stays powered (PD_BIAS=1 in CONFIG3)
     * so re-enabling is instantaneous — no re-settle needed. */
    ads_wreg(REG_BIAS_SENSP, enable ? BIAS_SENS_CH1_6 : 0x00);
    ads_wreg(REG_BIAS_SENSN, enable ? BIAS_SENS_CH1_6 : 0x00);
    ads_cmd(CMD_RDATAC);

    drl_on = enable;
    LOG_INF("DRL %s", enable ? "enabled (CH1–CH6 driving BIAS amp)"
                              : "disabled (BIAS amp input disconnected)");
}

bool eeg_drl_active(void)
{
    return drl_on;
}
