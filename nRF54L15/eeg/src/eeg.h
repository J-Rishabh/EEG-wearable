#ifndef EEG_H
#define EEG_H

#include <stdint.h>
#include <stdbool.h>

/* ---------- Channel / hardware constants ----------
 *
 * Physical channel assignment (per schematic):
 *   CH1  IN1P/N  EOG electrode
 *   CH2  IN2P/N  EMG_far electrode
 *   CH3  IN3P/N  EMG_near electrode
 *   CH4  IN4P/N  EEG_L1 (posterior)
 *   CH5  IN5P/N  EEG_L2 (central)
 *   CH6  IN6P/N  EEG_L3 (frontal/lateral)
 *   CH7  IN7P/N  UNUSED — tied to AVDD, powered down
 *   CH8  IN8P/N  BIAS_MEAS — monitors BIAS_IN common-mode (GAIN=1)
 *
 * Reference:  SRB1 pin → reference electrode; MISC1[5]=SRB1=1 connects SRB1
 *             to the inverting input of all channels (referential montage).
 * DRL/BIAS:   BIASOUT pin → patient via protection resistor.
 *             BIAS amplifier input = average(CH1–CH6) via BIAS_SENSP/N=0x3F.
 *             BIASREF_INT=1 in CONFIG3 — internal AVDD/2 reference.
 */

#define EEG_NUM_CHANNELS   8
#define EEG_VREF_MV        4500   /* internal reference: 4.5 V (PD_REFBUF=1) */
#define EEG_GAIN_DEFAULT   24     /* PGA gain for CH1–CH6: GAIN[2:0]=110 */
#define EEG_NUM_GAIN_GROUPS 4     /* number of independently-controlled gain groups */

/* Number of samples packed into one BLE notification */
#define EEG_BATCH_SIZE     8
/* Bytes per BLE packet: 2 (idx) + 4 (one gain byte per group) + 8*8*3 (int24 data) = 198 */
#define EEG_PACKET_BYTES   198

/* ---------- Data types ---------- */

/* One ADS1299 conversion frame */
struct eeg_sample {
    int32_t  ch[EEG_NUM_CHANNELS]; /* raw 24-bit signed, sign-extended to 32 bits */
    uint32_t status;               /* ADS1299 status word bits [23:0] */
};

/* ---------- API ---------- */

/**
 * Initialize the ADS1299.
 *
 * Full startup sequence:
 *   PWDN↑ → RESET pulse → SDATAC → ID verify (0x3E) →
 *   CONFIG3/1/2 → CHnSET × 8 → BIAS_SENSP/N → MISC1 →
 *   DRDY interrupt → START↑ → RDATAC
 *
 * @return 0 on success, negative errno on failure.
 */
int  eeg_init(void);

/** True if init succeeded and the ADS1299 is streaming at 250 SPS. */
bool eeg_is_ready(void);

/**
 * Convert a raw 24-bit value to microvolts using the current PGA gain.
 * µV = raw × VREF_µV / GAIN / 2^23
 */
int32_t eeg_raw_to_uv(int32_t raw);

/**
 * Drain up to @count samples from the internal ring buffer into @out.
 * Called from the main loop — not ISR-safe.
 * @return number of complete samples copied (0 if fewer than @count available).
 */
int eeg_read(struct eeg_sample *out, int count);

/**
 * Pack @EEG_BATCH_SIZE samples into a 198-byte BLE notification payload.
 *
 * Packet layout:
 *   [0–1]   sample index uint16 LE  (gap detection on the Python side)
 *   [2–5]   current PGA gain bytes (one per group, groups 0–3)
 *   [6–197] 8 samples × 8 channels × 3 bytes int24 big-endian
 *
 * @param batch   Array of exactly EEG_BATCH_SIZE samples (from eeg_read).
 * @param packet  Output buffer of EEG_PACKET_BYTES bytes.
 */
void eeg_pack_batch(const struct eeg_sample batch[EEG_BATCH_SIZE],
                    uint8_t packet[EEG_PACKET_BYTES]);

/**
 * Change PGA gain for one channel group.
 *
 * Groups:
 *   0 → CH1        (EOG)
 *   1 → CH2, CH3   (EMG / ECG)
 *   2 → CH4, CH6   (EEG L1 / L3)
 *   3 → CH5        (EEG L2)
 *
 * Issues SDATAC → WREG × n → RDATAC (~60 µs dead time).
 * Must be called from the system workqueue, not a BLE callback.
 *
 * @param group  0–3
 * @param gain   One of: 1, 2, 4, 6, 8, 12, 24.
 * @return 0 on success, -EINVAL for bad group or gain.
 */
int eeg_set_group_gain(uint8_t group, uint8_t gain);

/** Returns the current PGA gain for the given group (0–3). */
uint8_t eeg_get_group_gain(uint8_t group);

/**
 * Switch ADS1299 CH1–CH6 between normal electrode input and the internal
 * ~1 Hz calibration square wave (CONFIG2 INT_CAL=1, CAL_AMP=00 → ±1.875 mV).
 * Gains are preserved across the switch.  CH7 stays powered-down; CH8 BIAS_MEAS
 * is unaffected.  Must be called from the system workqueue, not a BLE callback.
 */
void eeg_set_test_mode(bool enable);

/** True if eeg_set_test_mode(true) has been called and not yet reversed. */
bool eeg_test_mode_active(void);

/**
 * Enable or disable the DRL (Driven Right Leg) circuit.
 * enable=true  → BIAS_SENSP/SENSN = 0x3F (CH1–CH6 feed the BIAS amplifier → active DRL)
 * enable=false → BIAS_SENSP/SENSN = 0x00 (BIAS amplifier still powered but drives nothing)
 * Must be called from the system workqueue, not a BLE callback.
 */
void eeg_set_drl(bool enable);

/** True if DRL is currently active (default: true after eeg_init). */
bool eeg_drl_active(void);

#endif /* EEG_H */
