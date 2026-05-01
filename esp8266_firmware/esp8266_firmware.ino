// =============================================================================
//  esp8266_firmware.ino
//  Nivixsa IoT Dashboard — ESP8266 NodeMCU provisioning firmware
// =============================================================================

#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <DNSServer.h>
#include <WiFiClientSecureBearSSL.h>
#include <ESP8266HTTPClient.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

#include "config.h"
#include "credentials.h"
#include "webpages.h"

CredentialStore credStore;
AppCredentials  creds;

ESP8266WebServer server(SERVER_PORT);
DNSServer dnsServer;

WiFiClient mqttWifiClient;
PubSubClient mqttClient(mqttWifiClient);

enum AppMode { MODE_SETUP, MODE_NORMAL };
AppMode appMode = MODE_SETUP;

bool mqttConnected = false;
bool pinUnlocked = false;
unsigned long msgCount = 0;
unsigned long lastMqttRetry = 0;
unsigned long startupMs = 0;

struct MqttMsg { String topic; String payload; };
#define MSG_BUF_SIZE 20
MqttMsg msgBuf[MSG_BUF_SIZE];
int msgBufHead = 0;

String chipID() {
  char id[9];
  snprintf(id, sizeof(id), "%08X", ESP.getChipId());
  return String(id);
}

String uptimeStr() {
  unsigned long s = (millis() - startupMs) / 1000;
  unsigned long m = s / 60; s %= 60;
  unsigned long h = m / 60; m %= 60;
  char buf[24];
  snprintf(buf, sizeof(buf), "%02luh %02lum %02lus", h, m, s);
  return String(buf);
}

void pushMsg(const String &topic, const String &payload) {
  msgBuf[msgBufHead] = {topic, payload};
  msgBufHead = (msgBufHead + 1) % MSG_BUF_SIZE;
  msgCount++;
}

void startCaptivePortal() {
  dnsServer.start(DNS_PORT, "*", AP_IP);
}

void handleScan() {
  int n = WiFi.scanNetworks(false, false);
  String json = "[";
  for (int i = 0; i < n; i++) {
    if (i) json += ",";
    json += "{\"ssid\":\"" + WiFi.SSID(i) + "\",";
    json += "\"rssi\":" + String(WiFi.RSSI(i)) + ",";
    json += "\"enc\":" + String(WiFi.encryptionType(i) != ENC_TYPE_NONE ? "true" : "false") + "}";
  }
  json += "]";
  server.send(200, "application/json", json);
}

void handleCheckPin() {
  String pin = server.arg("pin");
  if (pin == creds.setupPin) {
    pinUnlocked = true;
    server.send(200, "application/json", "{\"ok\":true}");
  } else {
    server.send(200, "application/json", "{\"ok\":false}");
  }
}

void handleSave() {
  if (!pinUnlocked) {
    server.send(403, "application/json", "{\"ok\":false,\"msg\":\"PIN required\"}");
    return;
  }
  if (!server.hasArg("plain")) {
    server.send(400, "application/json", "{\"ok\":false,\"msg\":\"No body\"}");
    return;
  }

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, server.arg("plain"));
  if (err) {
    server.send(400, "application/json", "{\"ok\":false,\"msg\":\"JSON parse error\"}");
    return;
  }

  String ssid   = doc["ssid"]   | "";
  String wpass  = doc["wpass"]  | "";
  String email  = doc["email"]  | "";
  String mpass  = doc["mpass"]  | "";
  String newpin = doc["newpin"] | "";

  if (ssid.isEmpty() || email.isEmpty() || mpass.isEmpty()) {
    server.send(400, "application/json", "{\"ok\":false,\"msg\":\"ssid/email/mpass required\"}");
    return;
  }

  credStore.saveWifiMqtt(ssid, wpass, email, mpass);
  if (!newpin.isEmpty()) {
    credStore.savePin(newpin);
  }

  server.send(200, "application/json", "{\"ok\":true}");
  delay(500);
  ESP.restart();
}

void handleSetupPage() {
  pinUnlocked = false;
  credStore.load(creds);
  server.send(200, "text/html", FPSTR(SETUP_HTML));
}

void handleDashboard() {
  String msgs;
  for (int i = 0; i < MSG_BUF_SIZE; i++) {
    int idx = ((msgBufHead - 1 - i) + MSG_BUF_SIZE) % MSG_BUF_SIZE;
    if (msgBuf[idx].topic.isEmpty()) continue;
    msgs += "<div class='msg-item'><span class='ts'>" + String(msgCount - i) + "</span>";
    msgs += "<span class='topic'>" + msgBuf[idx].topic + "</span> -> ";
    msgs += msgBuf[idx].payload.substring(0, 120) + "</div>";
  }
  if (msgs.isEmpty()) {
    msgs = "<p style='color:#64748b;font-size:.8rem'>No messages yet.</p>";
  }

  String html = FPSTR(DASHBOARD_HTML);
  html.replace("__WIFI_SSID__", creds.wifiSSID);
  html.replace("__IP__", WiFi.localIP().toString());
  html.replace("__RSSI__", String(WiFi.RSSI()));
  html.replace("__BROKER__", creds.mqttBroker);
  html.replace("__PORT__", String(creds.mqttPort));
  html.replace("__EMAIL__", creds.mqttEmail);
  html.replace("__MQTT_CLASS__", mqttConnected ? "on" : "off");
  html.replace("__MQTT_STATUS__", mqttConnected ? "Connected" : "Disconnected");
  html.replace("__MSG_COUNT__", String(msgCount));
  html.replace("__UPTIME__", uptimeStr());
  html.replace("__HEAP__", String(ESP.getFreeHeap() / 1024));
  html.replace("__MESSAGES__", msgs);
  server.send(200, "text/html", html);
}

void handleReset() {
  credStore.clear();
  server.send(200, "text/html", "<html><body style='font-family:sans-serif;padding:20px'>Reset done. Rebooting...</body></html>");
  delay(1000);
  ESP.restart();
}

void handleCaptivePortalRedirect() {
  server.sendHeader("Location", "http://192.168.4.1/", true);
  server.send(302, "text/plain", "");
}

bool callLoginApi() {
  Serial.println("[API] Calling Nivixsa login API...");

  BearSSL::WiFiClientSecure secureClient;
  secureClient.setInsecure();

  HTTPClient https;
  if (!https.begin(secureClient, CADIO_LOGIN_URL)) {
    Serial.println("[API] Failed to begin HTTPS");
    return false;
  }

  https.addHeader("Content-Type", "application/json");

  JsonDocument req;
  req["email"] = creds.mqttEmail;
  req["password"] = creds.mqttPassword;
  String body;
  serializeJson(req, body);

  int code = https.POST(body);
  if (code != 200) {
    Serial.printf("[API] HTTP %d\n", code);
    https.end();
    return false;
  }

  String response = https.getString();
  https.end();

  JsonDocument resp;
  DeserializationError err = deserializeJson(resp, response);
  if (err) {
    Serial.println("[API] JSON parse error");
    return false;
  }

  const char *broker = resp["broker"] | resp["mqtt_broker"] | DEFAULT_MQTT_BROKER;
  int port = resp["port"] | resp["mqtt_port"] | DEFAULT_MQTT_PORT;
  const char *prefix = resp["discovery_prefix"] | DISCOVERY_PREFIX;

  creds.mqttBroker = String(broker);
  creds.mqttPort = port;
  creds.discoveryPrefix = String(prefix);
  credStore.saveBrokerInfo(creds.mqttBroker, creds.mqttPort, creds.discoveryPrefix);

  Serial.printf("[API] Broker: %s:%d prefix: %s\n", creds.mqttBroker.c_str(), creds.mqttPort, creds.discoveryPrefix.c_str());
  return true;
}

void mqttCallback(char *topic, byte *payload, unsigned int length) {
  String t(topic);
  String p;
  p.reserve(length);
  for (unsigned int i = 0; i < length; i++) {
    p += (char)payload[i];
  }
  pushMsg(t, p);
  Serial.printf("[MQTT] %s -> %s\n", t.c_str(), p.substring(0, 80).c_str());
}

bool connectMqtt() {
  if (mqttClient.connected()) return true;

  String clientId = String(MQTT_CLIENT_ID_PREFIX) + chipID();
  mqttClient.setServer(creds.mqttBroker.c_str(), creds.mqttPort);
  mqttClient.setCallback(mqttCallback);
  mqttClient.setKeepAlive(MQTT_KEEPALIVE);
  mqttClient.setBufferSize(MQTT_BUFFER_SIZE);

  Serial.printf("[MQTT] Connecting to %s:%d as %s\n", creds.mqttBroker.c_str(), creds.mqttPort, clientId.c_str());

  bool ok = mqttClient.connect(clientId.c_str(), creds.mqttEmail.c_str(), creds.mqttPassword.c_str());
  if (!ok) {
    Serial.printf("[MQTT] Connect failed rc=%d\n", mqttClient.state());
    return false;
  }

  mqttConnected = true;
  Serial.println("[MQTT] Connected.");

  const char *components[] = {
    "sensor", "binary_sensor", "switch", "light", "climate", "cover",
    "fan", "lock", "button", "number", "select", "text", "vacuum",
    "alarm_control_panel", "camera", "humidifier", "update", "valve",
    "water_heater", "lawn_mower", "siren", "scene", "event", "notify",
    "device_tracker", "device_automation", "image", nullptr
  };

  String prefix = creds.discoveryPrefix;
  for (int i = 0; components[i] != nullptr; i++) {
    String topic = prefix + "/" + components[i] + "/+/+/#";
    mqttClient.subscribe(topic.c_str());
  }
  mqttClient.subscribe((prefix + "/status").c_str());

  return true;
}

void startAPMode() {
  Serial.println("[MODE] AP setup mode");
  appMode = MODE_SETUP;

  WiFi.mode(WIFI_AP);
  WiFi.softAPConfig(AP_IP, AP_GATEWAY, AP_SUBNET);

  String apSSID = String(AP_SSID_PREFIX) + "-" + chipID().substring(4);
  if (strlen(AP_PASSWORD) > 0) {
    WiFi.softAP(apSSID.c_str(), AP_PASSWORD);
  } else {
    WiFi.softAP(apSSID.c_str());
  }

  Serial.printf("[AP] SSID: %s IP: %s\n", apSSID.c_str(), AP_IP.toString().c_str());

  startCaptivePortal();

  server.on("/", HTTP_GET, handleSetupPage);
  server.on("/setup", HTTP_GET, handleSetupPage);
  server.on("/scan", HTTP_GET, handleScan);
  server.on("/check_pin", HTTP_GET, handleCheckPin);
  server.on("/save", HTTP_POST, handleSave);
  server.onNotFound(handleCaptivePortalRedirect);
  server.begin();
}

void startNormalMode() {
  Serial.println("[MODE] Normal mode");
  appMode = MODE_NORMAL;

  server.on("/", HTTP_GET, handleDashboard);
  server.on("/setup", HTTP_GET, handleSetupPage);
  server.on("/scan", HTTP_GET, handleScan);
  server.on("/check_pin", HTTP_GET, handleCheckPin);
  server.on("/save", HTTP_POST, handleSave);
  server.on("/reset", HTTP_GET, handleReset);
  server.onNotFound([]() { server.send(404, "text/plain", "Not found"); });
  server.begin();

  Serial.printf("[WEB] Dashboard: http://%s/\n", WiFi.localIP().toString().c_str());
}

void checkResetButton() {
  if (digitalRead(RESET_BTN_PIN) != LOW) return;

  unsigned long pressedAt = millis();
  while (digitalRead(RESET_BTN_PIN) == LOW) {
    if (millis() - pressedAt >= RESET_HOLD_MS) {
      Serial.println("[BTN] Reset triggered. Clearing credentials.");
      credStore.clear();
      delay(250);
      ESP.restart();
    }
    delay(50);
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);
  startupMs = millis();

  Serial.println("\n=== Nivixsa IoT Dashboard ESP8266 ===");
  pinMode(RESET_BTN_PIN, INPUT_PULLUP);

  bool hasCredentials = credStore.load(creds);
  if (!hasCredentials) {
    startAPMode();
    return;
  }

  WiFi.mode(WIFI_STA);
  WiFi.begin(creds.wifiSSID.c_str(), creds.wifiPassword.c_str());

  Serial.printf("[WiFi] Connecting to %s", creds.wifiSSID.c_str());
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - t0 > WIFI_CONNECT_TIMEOUT_MS) {
      Serial.println("\n[WiFi] Timeout. Falling back to AP mode.");
      startAPMode();
      return;
    }
    Serial.print(".");
    delay(250);
  }

  Serial.printf("\n[WiFi] Connected. IP: %s\n", WiFi.localIP().toString().c_str());

  if (!callLoginApi()) {
    Serial.println("[API] Failed. Using cached broker settings.");
  }

  connectMqtt();
  startNormalMode();
}

void loop() {
  checkResetButton();

  if (appMode == MODE_SETUP) {
    dnsServer.processNextRequest();
    server.handleClient();
    return;
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Disconnected. Restarting to recover.");
    delay(500);
    ESP.restart();
  }

  if (!mqttClient.connected()) {
    mqttConnected = false;
    unsigned long now = millis();
    if (now - lastMqttRetry >= MQTT_RECONNECT_INTERVAL_MS) {
      lastMqttRetry = now;
      connectMqtt();
    }
  }

  mqttClient.loop();
  server.handleClient();
}
