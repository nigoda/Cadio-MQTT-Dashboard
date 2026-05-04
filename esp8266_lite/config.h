// =============================================================================
//  config.h — Compile-time constants for ESP8266 Lite firmware
// =============================================================================
#pragma once

// ---------- Hardware --------------------------------------------------------
#define RESET_BTN_PIN      0     // GPIO0 (FLASH button on NodeMCU)
#define RESET_HOLD_MS   3000
#define STATUS_LED_PIN     2     // GPIO2 (D4), active LOW

// ---------- Access Point (setup mode) --------------------------------------
#define AP_SSID_PREFIX    "Nivixsa-Setup"
#define AP_PASSWORD       ""
#define AP_IP             IPAddress(192, 168, 4, 1)
#define AP_GATEWAY        IPAddress(192, 168, 4, 1)
#define AP_SUBNET         IPAddress(255, 255, 255, 0)
#define DNS_PORT          53

// ---------- Setup page PIN --------------------------------------------------
#define DEFAULT_SETUP_PIN "1234"

// ---------- Timeouts --------------------------------------------------------
#define WIFI_CONNECT_TIMEOUT_MS     30000
#define MQTT_RECONNECT_INTERVAL_MS   5000
#define SERVER_PORT                    80

// ---------- Cloud API -------------------------------------------------------
#define CADIO_LOGIN_URL "https://egycad.com/apis/cadio/login"

// ---------- MQTT defaults ---------------------------------------------------
#define DEFAULT_MQTT_BROKER   "egycad.com"
#define DEFAULT_MQTT_PORT     1883
#define DISCOVERY_PREFIX      "homeassistant"
#define MQTT_CLIENT_ID_PREFIX "esp8266-niv-"
#define MQTT_KEEPALIVE        60
#define MQTT_BUFFER_SIZE      2048

// ---------- Device limits ---------------------------------------------------
#define MAX_DEVICES     30
#define MSG_BUF_SIZE    10
