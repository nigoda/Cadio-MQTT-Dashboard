// =============================================================================
//  credentials.h — EEPROM credential storage for ESP8266 Lite
//  Now includes unitSerial field for serial number filtering.
// =============================================================================
#pragma once

#include <Arduino.h>
#include <EEPROM.h>
#include "config.h"

#define EEPROM_SIZE 1024
#define CRED_MAGIC  0x4E495632UL  // 'NIV2' — different from old firmware

struct AppCredentials {
  String wifiSSID;
  String wifiPassword;
  String mqttEmail;
  String mqttPassword;
  String setupPin;
  String mqttBroker;
  int    mqttPort;
  String discoveryPrefix;
  String unitSerial;       // Serial number of the physical unit to monitor
};

struct StoredCredentials {
  uint32_t magic;
  char wifiSSID[64];
  char wifiPassword[64];
  char mqttEmail[96];
  char mqttPassword[96];
  char setupPin[24];
  char mqttBroker[64];
  int32_t mqttPort;
  char discoveryPrefix[32];
  char unitSerial[48];     // e.g. "A4CF12F03246"
};

class CredentialStore {
public:
  bool load(AppCredentials &c) {
    StoredCredentials s;
    EEPROM.begin(EEPROM_SIZE);
    EEPROM.get(0, s);
    EEPROM.end();

    if (s.magic != CRED_MAGIC) {
      c.wifiSSID        = "";
      c.wifiPassword    = "";
      c.mqttEmail       = "";
      c.mqttPassword    = "";
      c.setupPin        = DEFAULT_SETUP_PIN;
      c.mqttBroker      = DEFAULT_MQTT_BROKER;
      c.mqttPort        = DEFAULT_MQTT_PORT;
      c.discoveryPrefix = DISCOVERY_PREFIX;
      c.unitSerial      = "";
      return false;
    }

    c.wifiSSID        = String(s.wifiSSID);
    c.wifiPassword    = String(s.wifiPassword);
    c.mqttEmail       = String(s.mqttEmail);
    c.mqttPassword    = String(s.mqttPassword);
    c.setupPin        = String(s.setupPin[0]        ? s.setupPin        : DEFAULT_SETUP_PIN);
    c.mqttBroker      = String(s.mqttBroker[0]      ? s.mqttBroker      : DEFAULT_MQTT_BROKER);
    c.mqttPort        = s.mqttPort > 0 ? s.mqttPort : DEFAULT_MQTT_PORT;
    c.discoveryPrefix = String(s.discoveryPrefix[0] ? s.discoveryPrefix : DISCOVERY_PREFIX);
    c.unitSerial      = String(s.unitSerial);

    return (c.wifiSSID.length() > 0 && c.mqttEmail.length() > 0);
  }

  void saveAll(const String &ssid, const String &wpass,
               const String &email, const String &mpass,
               const String &serial, const String &pin) {
    StoredCredentials s = readOrDefault();
    cp(s.wifiSSID,     sizeof(s.wifiSSID),     ssid);
    cp(s.wifiPassword, sizeof(s.wifiPassword), wpass);
    cp(s.mqttEmail,    sizeof(s.mqttEmail),    email);
    cp(s.mqttPassword, sizeof(s.mqttPassword), mpass);
    cp(s.unitSerial,   sizeof(s.unitSerial),   serial);
    if (pin.length() > 0) cp(s.setupPin, sizeof(s.setupPin), pin);
    write(s);
  }

  void saveBrokerInfo(const String &broker, int port, const String &prefix) {
    StoredCredentials s = readOrDefault();
    cp(s.mqttBroker,       sizeof(s.mqttBroker),       broker);
    s.mqttPort = port;
    cp(s.discoveryPrefix,  sizeof(s.discoveryPrefix),  prefix);
    write(s);
  }

  void clear() {
    StoredCredentials s;
    memset(&s, 0, sizeof(s));
    write(s);
  }

private:
  static void cp(char *dst, size_t n, const String &src) {
    if (n == 0) return;
    strncpy(dst, src.c_str(), n - 1);
    dst[n - 1] = '\0';
  }

  StoredCredentials readOrDefault() {
    StoredCredentials s;
    EEPROM.begin(EEPROM_SIZE);
    EEPROM.get(0, s);
    EEPROM.end();
    if (s.magic != CRED_MAGIC) {
      memset(&s, 0, sizeof(s));
      s.magic = CRED_MAGIC;
      cp(s.setupPin,        sizeof(s.setupPin),        DEFAULT_SETUP_PIN);
      cp(s.mqttBroker,      sizeof(s.mqttBroker),      DEFAULT_MQTT_BROKER);
      s.mqttPort = DEFAULT_MQTT_PORT;
      cp(s.discoveryPrefix, sizeof(s.discoveryPrefix), DISCOVERY_PREFIX);
    }
    return s;
  }

  void write(const StoredCredentials &s) {
    EEPROM.begin(EEPROM_SIZE);
    EEPROM.put(0, s);
    EEPROM.commit();
    EEPROM.end();
  }
};
