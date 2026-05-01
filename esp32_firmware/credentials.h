// =============================================================================
//  credentials.h — Non-volatile credential storage (ESP32 Preferences / NVS)
// =============================================================================
#pragma once
#include <Preferences.h>

// Namespace inside NVS — max 15 chars
#define NVS_NS "nivixsa"

struct AppCredentials {
  String wifiSSID;
  String wifiPassword;
  String mqttEmail;
  String mqttPassword;
  String setupPin;
  // Populated by the login API — cached so we can reconnect without calling the API again
  String mqttBroker;
  int    mqttPort;
  String discoveryPrefix;
};

class CredentialStore {
public:
  // ---- Load ---------------------------------------------------------------
  bool load(AppCredentials &c) {
    Preferences p;
    p.begin(NVS_NS, /*readOnly=*/true);
    c.wifiSSID        = p.getString("wifi_ssid",   "");
    c.wifiPassword    = p.getString("wifi_pass",   "");
    c.mqttEmail       = p.getString("mqtt_email",  "");
    c.mqttPassword    = p.getString("mqtt_pass",   "");
    c.setupPin        = p.getString("setup_pin",   DEFAULT_SETUP_PIN);
    c.mqttBroker      = p.getString("mqtt_broker", DEFAULT_MQTT_BROKER);
    c.mqttPort        = p.getInt   ("mqtt_port",   DEFAULT_MQTT_PORT);
    c.discoveryPrefix = p.getString("disc_prefix", DISCOVERY_PREFIX);
    p.end();
    return (c.wifiSSID.length() > 0 && c.mqttEmail.length() > 0);
  }

  // ---- Save Wi-Fi + MQTT login credentials --------------------------------
  void saveWifiMqtt(const String &ssid, const String &pass,
                    const String &email, const String &mqttPass) {
    Preferences p;
    p.begin(NVS_NS, /*readOnly=*/false);
    p.putString("wifi_ssid",  ssid);
    p.putString("wifi_pass",  pass);
    p.putString("mqtt_email", email);
    p.putString("mqtt_pass",  mqttPass);
    p.end();
  }

  // ---- Cache broker details returned by the login API --------------------
  void saveBrokerInfo(const String &broker, int port, const String &prefix) {
    Preferences p;
    p.begin(NVS_NS, /*readOnly=*/false);
    p.putString("mqtt_broker", broker);
    p.putInt   ("mqtt_port",   port);
    p.putString("disc_prefix", prefix);
    p.end();
  }

  // ---- Update setup PIN ---------------------------------------------------
  void savePin(const String &pin) {
    Preferences p;
    p.begin(NVS_NS, /*readOnly=*/false);
    p.putString("setup_pin", pin);
    p.end();
  }

  // ---- Wipe everything (factory reset) -----------------------------------
  void clear() {
    Preferences p;
    p.begin(NVS_NS, /*readOnly=*/false);
    p.clear();
    p.end();
  }
};
