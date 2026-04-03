#include "ble.h"

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

#define BT_UUID_EEG_SVC_VAL \
    BT_UUID_128_ENCODE(0x12340001, 0x1234, 0x1234, 0x1234, 0x123456789abcULL)
#define BT_UUID_EEG_DATA_VAL \
    BT_UUID_128_ENCODE(0x12340002, 0x1234, 0x1234, 0x1234, 0x123456789abcULL)

static struct bt_uuid_128 eeg_svc_uuid  = BT_UUID_INIT_128(BT_UUID_EEG_SVC_VAL);
static struct bt_uuid_128 eeg_data_uuid = BT_UUID_INIT_128(BT_UUID_EEG_DATA_VAL);

/* ---------- State ---------- */

static struct bt_conn *current_conn;
static bool eeg_subscribed;

/* ---------- GATT service ---------- */

static void eeg_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
    eeg_subscribed = (value & BT_GATT_CCC_NOTIFY) != 0;
    LOG_INF("EEG notify %s", eeg_subscribed ? "enabled" : "disabled");
}

/*
 * attrs layout:
 *   [0] primary service declaration
 *   [1] characteristic declaration
 *   [2] characteristic value  ← bt_gatt_notify() target
 *   [3] CCC descriptor
 */
BT_GATT_SERVICE_DEFINE(eeg_svc,
    BT_GATT_PRIMARY_SERVICE(&eeg_svc_uuid),
    BT_GATT_CHARACTERISTIC(&eeg_data_uuid.uuid,
                           BT_GATT_CHRC_NOTIFY,
                           BT_GATT_PERM_NONE,
                           NULL, NULL, NULL),
    BT_GATT_CCC(eeg_ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
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
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
    LOG_INF("Disconnected (reason 0x%02x)", reason);

    if (current_conn) {
        bt_conn_unref(current_conn);
        current_conn = NULL;
    }
    eeg_subscribed = false;

    /* Restart advertising so the phone can reconnect. */
    int err = bt_le_adv_start(&adv_param, ad, ARRAY_SIZE(ad),
                               sd, ARRAY_SIZE(sd));
    if (err) {
        LOG_ERR("Re-advertising failed (err %d)", err);
    }
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
