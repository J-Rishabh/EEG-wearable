#include "ble.h"
#include "imu.h"
#include "eeg.h"

#include <zephyr/sys/byteorder.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(ble, LOG_LEVEL_INF);

/* ---------- UUIDs ---------- */

/* EEG Service:      12340001-1234-1234-1234-123456789abc */
/* EEG Data Char:    12340002-1234-1234-1234-123456789abc */
/* IMU Data Char:    12340003-1234-1234-1234-123456789abc */
/* Gain Control:     12340004-1234-1234-1234-123456789abc */

#define BT_UUID_EEG_SVC_VAL \
    BT_UUID_128_ENCODE(0x12340001, 0x1234, 0x1234, 0x1234, 0x123456789abcULL)
#define BT_UUID_EEG_DATA_VAL \
    BT_UUID_128_ENCODE(0x12340002, 0x1234, 0x1234, 0x1234, 0x123456789abcULL)
#define BT_UUID_IMU_DATA_VAL \
    BT_UUID_128_ENCODE(0x12340003, 0x1234, 0x1234, 0x1234, 0x123456789abcULL)
#define BT_UUID_CTRL_VAL \
    BT_UUID_128_ENCODE(0x12340004, 0x1234, 0x1234, 0x1234, 0x123456789abcULL)

static struct bt_uuid_128 eeg_svc_uuid  = BT_UUID_INIT_128(BT_UUID_EEG_SVC_VAL);
static struct bt_uuid_128 eeg_data_uuid = BT_UUID_INIT_128(BT_UUID_EEG_DATA_VAL);
static struct bt_uuid_128 imu_data_uuid = BT_UUID_INIT_128(BT_UUID_IMU_DATA_VAL);
static struct bt_uuid_128 ctrl_uuid     = BT_UUID_INIT_128(BT_UUID_CTRL_VAL);

/* ---------- State ---------- */

static struct bt_conn *current_conn;
static bool eeg_subscribed;
static bool imu_subscribed;

/* ---------- GATT service ---------- */

static void eeg_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
    eeg_subscribed = (value & BT_GATT_CCC_NOTIFY) != 0;
    LOG_INF("EEG notify %s", eeg_subscribed ? "enabled" : "disabled");
}

static void imu_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
    imu_subscribed = (value & BT_GATT_CCC_NOTIFY) != 0;
    LOG_INF("IMU notify %s", imu_subscribed ? "enabled" : "disabled");
}

/* ---------- Gain control write ----------
 *
 * The Python visualizer writes one byte (gain ∈ {1,2,4,6,8,12,24}) to the
 * Control characteristic (UUID 12340004) to change the ADS1299 PGA gain.
 *
 * The GATT write callback runs in the BLE RX thread; SPI must not be called
 * there.  We store the requested gain and post a k_work item so that
 * eeg_set_gain() runs in the system workqueue — the same queue as the DRDY
 * work handler — and the two SPI users are naturally serialised.
 */
static struct k_work  gain_work;
static uint8_t        pending_gain;
static uint8_t        pending_group;

static void gain_work_handler(struct k_work *work)
{
    ARG_UNUSED(work);
    if (!eeg_is_ready()) {
        LOG_WRN("Gain write ignored — EEG not ready");
        return;
    }
    eeg_set_group_gain(pending_group, pending_gain);
}

/* ---------- Test mode work ----------
 *
 * Byte 0 = 0xFF in a ctrl_write triggers test mode.
 * Byte 1 = 0x01 → enable internal calibration signal; 0x00 → restore electrode input.
 * Runs in the system workqueue so SPI is safe to call.
 */
static struct k_work test_work;
static bool          pending_test_mode;

static void test_work_handler(struct k_work *work)
{
    ARG_UNUSED(work);
    if (!eeg_is_ready()) {
        LOG_WRN("Test mode write ignored — EEG not ready");
        return;
    }
    eeg_set_test_mode(pending_test_mode);
}

static ssize_t ctrl_write(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                           const void *buf, uint16_t len,
                           uint16_t offset, uint8_t flags)
{
    ARG_UNUSED(attr);
    ARG_UNUSED(flags);

    if (offset != 0) {
        return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
    }
    if (len != 2) {
        return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
    }

    uint8_t grp = ((const uint8_t *)buf)[0];
    uint8_t g   = ((const uint8_t *)buf)[1];

    /* 0xFF = test mode command; byte 1 = 0 (disable) or 1 (enable). */
    if (grp == 0xFF) {
        if (g != 0 && g != 1) {
            LOG_WRN("Test mode rejected: byte 1 must be 0 or 1, got %u", g);
            return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);
        }
        pending_test_mode = (g == 1);
        k_work_submit(&test_work);
        LOG_INF("Test mode request: %s", g ? "enable" : "disable");
        return (ssize_t)len;
    }

    if (grp >= 4) {
        LOG_WRN("Gain write rejected: group %u out of range (0–3)", grp);
        return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);
    }

    static const uint8_t valid_gains[] = { 1, 2, 4, 6, 8, 12, 24 };
    bool ok = false;
    for (int i = 0; i < (int)ARRAY_SIZE(valid_gains); i++) {
        if (valid_gains[i] == g) { ok = true; break; }
    }
    if (!ok) {
        LOG_WRN("Gain write rejected: %u not in {1,2,4,6,8,12,24}", g);
        return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);
    }

    pending_group = grp;
    pending_gain  = g;
    k_work_submit(&gain_work);
    LOG_INF("Gain change requested: group %u → %u", grp, g);
    return (ssize_t)len;
}

/*
 * attrs layout:
 *   [0] primary service declaration
 *   [1] EEG characteristic declaration
 *   [2] EEG characteristic value  ← bt_gatt_notify() target for EEG
 *   [3] EEG CCC descriptor
 *   [4] IMU characteristic declaration
 *   [5] IMU characteristic value  ← bt_gatt_notify() target for IMU
 *   [6] IMU CCC descriptor
 *   [7] Gain Control characteristic declaration
 *   [8] Gain Control characteristic value  ← write target (1 byte gain)
 */
BT_GATT_SERVICE_DEFINE(eeg_svc,
    BT_GATT_PRIMARY_SERVICE(&eeg_svc_uuid),
    BT_GATT_CHARACTERISTIC(&eeg_data_uuid.uuid,
                           BT_GATT_CHRC_NOTIFY,
                           BT_GATT_PERM_NONE,
                           NULL, NULL, NULL),
    BT_GATT_CCC(eeg_ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
    BT_GATT_CHARACTERISTIC(&imu_data_uuid.uuid,
                           BT_GATT_CHRC_NOTIFY,
                           BT_GATT_PERM_NONE,
                           NULL, NULL, NULL),
    BT_GATT_CCC(imu_ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
    BT_GATT_CHARACTERISTIC(&ctrl_uuid.uuid,
                           BT_GATT_CHRC_WRITE | BT_GATT_CHRC_WRITE_WITHOUT_RESP,
                           BT_GATT_PERM_WRITE,
                           NULL, ctrl_write, NULL),
);

/* ---------- Advertising data ---------- */

/* BT_LE_ADV_CONN was removed in NCS v2.6+ — define params explicitly. */
static const struct bt_le_adv_param adv_param =
    BT_LE_ADV_PARAM_INIT(BT_LE_ADV_OPT_CONN,
                         BT_GAP_ADV_FAST_INT_MIN_2,
                         BT_GAP_ADV_FAST_INT_MAX_2,
                         NULL);

static const struct bt_data ad[] = {
    BT_DATA_BYTES(BT_DATA_FLAGS, BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR),
    BT_DATA_BYTES(BT_DATA_UUID128_ALL, BT_UUID_EEG_SVC_VAL),
};

static const struct bt_data sd[] = {
    BT_DATA(BT_DATA_NAME_COMPLETE,
            CONFIG_BT_DEVICE_NAME,
            sizeof(CONFIG_BT_DEVICE_NAME) - 1),
};

/* ---------- Advertising work ----------
 * k_work used to restart advertising outside the BLE callback context.
 * Calling bt_le_adv_start() directly from disconnected() is unreliable —
 * the controller is still tearing down the connection when the callback
 * fires, so the call can silently fail. */
static struct k_work adv_work;

static void adv_work_handler(struct k_work *work)
{
    ARG_UNUSED(work);
    int err = bt_le_adv_start(&adv_param, ad, ARRAY_SIZE(ad),
                               sd, ARRAY_SIZE(sd));
    if (err) {
        LOG_ERR("Re-advertising failed (err %d)", err);
    } else {
        LOG_INF("Re-advertising started");
    }
}

/* ---------- Delayed DLE work ----------
 *
 * Windows' BLE stack initiates its own LL procedures (connection parameter
 * update) immediately after connecting.  If the nRF also fires a DLE request
 * at the same time (CONFIG_BT_DATA_LEN_UPDATE auto-behaviour) the two LL
 * procedures collide → 0x23 LL_PROC_COLLISION → disconnect.
 *
 * Fix: disable CONFIG_BT_DATA_LEN_UPDATE and request DLE manually after 3 s,
 * by which point Windows has finished all its own LL procedures.
 * Android/iOS are unaffected — they handle the collision gracefully.
 */
static struct k_work_delayable dle_work;

static void dle_work_handler(struct k_work *work)
{
    ARG_UNUSED(work);
    if (!current_conn) {
        return;
    }
    struct bt_conn_le_data_len_param param = {
        .tx_max_len  = BT_GAP_DATA_LEN_MAX,
        .tx_max_time = BT_GAP_DATA_TIME_MAX,
    };
    int err = bt_conn_le_data_len_update(current_conn, &param);
    if (err) {
        LOG_WRN("DLE update failed (%d)", err);
    } else {
        LOG_INF("DLE update requested (delayed for Windows compatibility)");
    }
}

/* ---------- Connection callbacks ---------- */

static void connected(struct bt_conn *conn, uint8_t err)
{
    if (err) {
        LOG_ERR("Connection failed (err %u)", err);
        return;
    }

    current_conn = bt_conn_ref(conn);
    LOG_INF("Connected");
    /* MTU is negotiated automatically by CONFIG_BT_GATT_AUTO_UPDATE_MTU. */

    /* Schedule DLE 3 s after connect — avoids 0x23 collision with Windows. */
    k_work_schedule(&dle_work, K_SECONDS(3));
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
    LOG_INF("Disconnected (reason 0x%02x)", reason);

    /* Cancel any pending DLE request for this connection. */
    k_work_cancel_delayable(&dle_work);

    if (current_conn) {
        bt_conn_unref(current_conn);
        current_conn = NULL;
    }
    eeg_subscribed = false;
    imu_subscribed = false;

    /* Defer advertising restart to the system workqueue so it runs after
     * the BLE stack finishes tearing down the connection. */
    k_work_submit(&adv_work);
}

BT_CONN_CB_DEFINE(conn_callbacks) = {
    .connected    = connected,
    .disconnected = disconnected,
};

/* ---------- Public API ---------- */

int ble_init(void)
{
    int err = bt_enable(NULL);
    if (err) {
        LOG_ERR("bt_enable failed (err %d)", err);
        return err;
    }

    k_work_init(&adv_work,  adv_work_handler);
    k_work_init(&gain_work, gain_work_handler);
    k_work_init(&test_work, test_work_handler);
    k_work_init_delayable(&dle_work, dle_work_handler);

    err = bt_le_adv_start(&adv_param, ad, ARRAY_SIZE(ad),
                           sd, ARRAY_SIZE(sd));
    if (err) {
        LOG_ERR("Advertising start failed (err %d)", err);
        return err;
    }

    LOG_INF("BLE advertising as \"%s\"", CONFIG_BT_DEVICE_NAME);
    return 0;
}

bool ble_is_connected(void)
{
    return current_conn != NULL;
}

bool ble_eeg_subscribed(void)
{
    return eeg_subscribed;
}

int ble_notify_eeg(const uint8_t *data, uint16_t len)
{
    if (current_conn == NULL) {
        return -ENOTCONN;
    }
    if (!eeg_subscribed) {
        return -EACCES;
    }
    return bt_gatt_notify(NULL, &eeg_svc.attrs[2], data, len);
}

bool ble_imu_subscribed(void)
{
    return imu_subscribed;
}

int ble_notify_imu(const struct imu_sample *sample)
{
    if (current_conn == NULL) {
        return -ENOTCONN;
    }
    if (!imu_subscribed) {
        return -EACCES;
    }
    /* Pack as 8-byte little-endian: x_mg, y_mg, z_mg, temp_cdeg (all int16). */
    uint8_t buf[8];
    sys_put_le16((uint16_t)sample->x_mg,    &buf[0]);
    sys_put_le16((uint16_t)sample->y_mg,    &buf[2]);
    sys_put_le16((uint16_t)sample->z_mg,    &buf[4]);
    sys_put_le16((uint16_t)sample->temp_cdeg, &buf[6]);
    return bt_gatt_notify(NULL, &eeg_svc.attrs[5], buf, sizeof(buf));
}
