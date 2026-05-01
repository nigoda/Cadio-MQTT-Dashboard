// =============================================================================
//  credentials.h — Non-volatile credential storage (ESP8266 EEPROM)
// =============================================================================
#pragma once

#include <Arduino.h>
#include <EEPROM.h>
#include "config.h"

#define EEPROM_SIZE 1024
#define CRED_MAGIC  0x4E495658UL  // 'NIVX'

struct AppCredentials {
  String wifiSSID;
  String wifiPassword;
  String mqttEmail;
  String mqttPassword;
  String setupPin;
  String mqttBroker;
  int    mqttPort;
  String discoveryPrefix;
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
};

class CredentialStore {
public:
  bool load(AppCredentials &c) {
    StoredCredentials s;
    EEPROM.begin(EEPROM_SIZE);
    EEPROM.get(0, s);
    EEPROM.end();

    if (s.magic != CRED_MAGIC) {
      c.wifiSSID = "";
      c.wifiPassword = "";
      c.mqttEmail = "";
      c.mqttPassword = "";
      c.setupPin = DEFAULT_SETUP_PIN;
      c.mqttBroker = DEFAULT_MQTT_BROKER;
      c.mqttPort = DEFAULT_MQTT_PORT;
      c.discoveryPrefix = DISCOVERY_PREFIX;
      return false;
    }

    c.wifiSSID = String(s.wifiSSID);
    c.wifiPassword = String(s.wifiPassword);
    c.mqttEmail = String(s.mqttEmail);
    c.mqttPassword = String(s.mqttPassword);
    c.setupPin = String(s.setupPin[0] ? s.setupPin : DEFAULT_SETUP_PIN);
    c.mqttBroker = String(s.mqttBroker[0] ? s.mqttBroker : DEFAULT_MQTT_BROKER);
    c.mqttPort = s.mqttPort > 0 ? s.mqttPort : DEFAULT_MQTT_PORT;
    c.discoveryPrefix = String(s.discoveryPrefix[0] ? s.discoveryPrefix : DISCOVERY_PREFIX);

    return (c.wifiSSID.length() > 0 && c.mqttEmail.length() > 0);
  }

  void saveWifiMqtt(const String &ssid, const String &pass,
                    const String &email, const String &mqttPass) {
    StoredCredentials s = readOrDefault();
    copyStr(s.wifiSSID, sizeof(s.wifiSSID), ssid);
    copyStr(s.wifiPassword, sizeof(s.wifiPassword), pass);
    copyStr(s.mqttEmail, sizeof(s.mqttEmail), email);
    copyStr(s.mqttPassword, sizeof(s.mqttPassword), mqttPass);
    writeStruct(s);
  }

  void saveBrokerInfo(const String &broker, int port, const String &prefix) {
    StoredCredentials s = readOrDefault();
    copyStr(s.mqttBroker, sizeof(s.mqttBroker), broker);
    s.mqttPort = port;
    copyStr(s.discoveryPrefix, sizeof(s.discoveryPrefix), prefix);
    writeStruct(s);
  }

  void savePin(const String &pin) {
    StoredCredentials s = readOrDefault();
    copyStr(s.setupPin, sizeof(s.setupPin), pin);
    writeStruct(s);
  }

  void clear() {
    StoredCredentials s;
    memset(&s, 0, sizeof(s));
    writeStruct(s);
  }

private:
  static void copyStr(char *dst, size_t dstSize, const String &src) {
    if (dstSize == 0) return;
    strncpy(dst, src.c_str(), dstSize - 1);
    dst[dstSize - 1] = '\0';
  }

  StoredCredentials readOrDefault() {
    StoredCredentials s;
    EEPROM.begin(EEPROM_SIZE);
    EEPROM.get(0, s);
    EEPROM.end();

    if (s.magic != CRED_MAGIC) {
      memset(&s, 0, sizeof(s));
      s.magic = CRED_MAGIC;
      copyStr(s.setupPin, sizeof(s.setupPin), DEFAULT_SETUP_PIN);
      copyStr(s.mqttBroker, sizeof(s.mqttBroker), DEFAULT_MQTT_BROKER);
      s.mqttPort = DEFAULT_MQTT_PORT;
      copyStr(s.discoveryPrefix, sizeof(s.discoveryPrefix), DISCOVERY_PREFIX);
    }
    return s;
  }

  void writeStruct(const StoredCredentials &s) {
    EEPROM.begin(EEPROM_SIZE);
    EEPROM.put(0, s);
    EEPROM.commit();
    EEPROM.end();
  }
};
