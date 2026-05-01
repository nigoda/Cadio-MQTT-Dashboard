// =============================================================================
//  config.h — ESP8266 NodeMCU pin assignments and compile-time constants
// =============================================================================
#pragma once

// ---------- Hardware --------------------------------------------------------
// Use the built-in FLASH button on NodeMCU: D3 (GPIO0).
// NOTE: GPIO0 is a boot strap pin; do not keep it pressed while powering on.
#define RESET_BTN_PIN      0
#define RESET_HOLD_MS   3000

// Built-in LED on NodeMCU: GPIO2 (D4), active LOW
#define STATUS_LED_PIN     2
// Blink patterns (milliseconds)
//   Fast blink  = AP / provisioning mode
//   Slow blink  = Wi-Fi connected, MQTT disconnected
//   Solid ON    = Wi-Fi + MQTT connected

// ---------- Access Point (setup mode) --------------------------------------
#define AP_SSID_PREFIX    "Nivixsa-Setup"
#define AP_PASSWORD       ""                // empty = open AP
#define AP_IP             IPAddress(192, 168, 4, 1)
#define AP_GATEWAY        IPAddress(192, 168, 4, 1)
#define AP_SUBNET         IPAddress(255, 255, 255, 0)
#define DNS_PORT          53

// ---------- Setup page PIN --------------------------------------------------
#define DEFAULT_SETUP_PIN "1234"

// ---------- Normal-mode timeouts --------------------------------------------
#define WIFI_CONNECT_TIMEOUT_MS     30000
#define WIFI_RETRY_INTERVAL_MS       5000
#define MQTT_RECONNECT_INTERVAL_MS   5000
#define SERVER_PORT                    80

// ---------- Nivixsa cloud API -----------------------------------------------
#define CADIO_LOGIN_URL "https://egycad.com/apis/cadio/login"

// ---------- MQTT defaults ----------------------------------------------------
#define DEFAULT_MQTT_BROKER   "egycad.com"
#define DEFAULT_MQTT_PORT     1883
#define DISCOVERY_PREFIX      "homeassistant"
#define MQTT_CLIENT_ID_PREFIX "esp8266-nivixsa-"
#define APP_MQTT_KEEPALIVE    60
#define MQTT_BUFFER_SIZE      2048
