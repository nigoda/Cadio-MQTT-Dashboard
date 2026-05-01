// =============================================================================
//  config.h — Pin assignments and compile-time constants
// =============================================================================
#pragma once

// ---------- Hardware --------------------------------------------------------
#define RESET_BTN_PIN     0       // GPIO0 = BOOT button on most ESP32 devkits
#define RESET_HOLD_MS  5000       // Hold this many ms to wipe credentials

// ---------- Access Point (setup mode) ---------------------------------------
#define AP_SSID_PREFIX    "Nivixsa-Setup"   // Final SSID = "Nivixsa-Setup-XXXX"
#define AP_PASSWORD       ""                // Empty = open AP  (add a password if preferred)
#define AP_IP             IPAddress(192, 168, 4, 1)
#define AP_GATEWAY        IPAddress(192, 168, 4, 1)
#define AP_SUBNET         IPAddress(255, 255, 255, 0)
#define DNS_PORT          53

// ---------- Setup page PIN --------------------------------------------------
// Users must enter this before they can change credentials.
// Change it here before flashing, or it can be updated via the web UI.
#define DEFAULT_SETUP_PIN "1234"

// ---------- Normal-mode timeouts --------------------------------------------
#define WIFI_CONNECT_TIMEOUT_MS   30000   // 30 s — fall back to AP mode if exceeded
#define MQTT_RECONNECT_INTERVAL_MS  5000  // retry MQTT every 5 s
#define SERVER_PORT                   80

// ---------- Nivixsa cloud API -----------------------------------------------
#define CADIO_LOGIN_URL "https://egycad.com/apis/cadio/login"

// ---------- MQTT defaults (overridden by login API response) ----------------
#define DEFAULT_MQTT_BROKER   "egycad.com"
#define DEFAULT_MQTT_PORT     1883
#define DISCOVERY_PREFIX      "homeassistant"
#define MQTT_CLIENT_ID_PREFIX "esp32-nivixsa-"
#define MQTT_KEEPALIVE        60
#define MQTT_BUFFER_SIZE    2048    // bytes — increase if large payloads are expected
