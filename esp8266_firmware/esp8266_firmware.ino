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

// ---------------------------------------------------------------------------
//  Device discovery — parsed from homeassistant MQTT config messages
// ---------------------------------------------------------------------------
#define MAX_DEVICES     20
#define DEV_NAME_LEN    48
#define DEV_TYPE_LEN    16
#define DEV_TOPIC_LEN   96
#define DEV_STATE_LEN   48

struct IoTDevice {
  bool   active;
  char   name[DEV_NAME_LEN];
  char   type[DEV_TYPE_LEN];       // switch, light, sensor, binary_sensor ...
  char   stateTopic[DEV_TOPIC_LEN];
  char   cmdTopic[DEV_TOPIC_LEN];
  char   state[DEV_STATE_LEN];
};

IoTDevice devices[MAX_DEVICES];
int deviceCount = 0;

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

// ---------------------------------------------------------------------------
//  /api/data — lightweight JSON endpoint polled by JS every 10s
//  Keeps heap usage low by never rebuilding the full HTML page
// ---------------------------------------------------------------------------
void handleApiData() {
  String json = "{";
  json += "\"rssi\":" + String(WiFi.RSSI()) + ",";
  json += "\"heap\":" + String(ESP.getFreeHeap() / 1024) + ",";
  json += "\"uptime\":\"" + uptimeStr() + "\",";
  json += "\"mqtt\":" + String(mqttConnected ? "true" : "false") + ",";
  json += "\"msg_count\":" + String(msgCount) + ",";

  // Recent messages array (last 10 only to keep JSON small)
  json += "\"messages\":[";
  bool firstMsg = true;
  for (int i = 0; i < 10; i++) {
    int idx = ((msgBufHead - 1 - i) + MSG_BUF_SIZE) % MSG_BUF_SIZE;
    if (msgBuf[idx].topic.isEmpty()) continue;
    if (!firstMsg) json += ",";
    firstMsg = false;
    String t = msgBuf[idx].topic;
    String p = msgBuf[idx].payload.substring(0, 80);
    t.replace("\\", "\\\\"); t.replace("\"", "\\\"");
    p.replace("\\", "\\\\"); p.replace("\"", "\\\"");
    json += "{\"t\":\"" + t + "\",\"p\":\"" + p + "\"}";
  }
  json += "],";

  // Devices array
  json += "\"devices\":[";
  bool firstDev = true;
  for (int i = 0; i < deviceCount; i++) {
    if (!devices[i].active) continue;
    if (!firstDev) json += ",";
    firstDev = false;
      String id    = String(devices[i].stateTopic);
      String name  = String(devices[i].name);  name.replace("\\", "\\\\"); name.replace("\"", "'");
      String type  = String(devices[i].type);  type.replace("\\", "\\\\"); type.replace("\"", "'");
      String state = String(devices[i].state); state.replace("\\", "\\\\"); state.replace("\"", "'");
      String cmd   = String(devices[i].cmdTopic); cmd.replace("\\", "\\\\"); cmd.replace("\"", "\\\"");
      id.replace("\\", "\\\\"); id.replace("\"", "\\\"");
    json += "{";
      json += "\"id\":\"" + id + "\",";
    json += "\"name\":\"" + name + "\",";
      json += "\"type\":\"" + type + "\",";
    json += "\"state\":\"" + state + "\",";
    json += "\"cmd\":\"" + cmd + "\"";
    json += "}";
  }
  json += "]}";

  server.sendHeader("Cache-Control", "no-cache");
  server.send(200, "application/json", json);
}

void handleCmd() {
  if (!server.hasArg("topic") || !server.hasArg("payload")) {
    server.send(400, "application/json", "{\"ok\":false,\"msg\":\"Missing topic or payload\"}");
    return;
  }
  if (!mqttClient.connected()) {
    server.send(503, "application/json", "{\"ok\":false,\"msg\":\"MQTT not connected\"}");
    return;
  }
  String topic   = server.arg("topic");
  String payload = server.arg("payload");
  String json    = "{\"state\":\"" + payload + "\"}";
  mqttClient.publish(topic.c_str(), json.c_str(), false);

  // Reflect the new state immediately in the local device cache so the UI
  // updates without waiting for the entity to publish a separate state echo.
  for (int i = 0; i < deviceCount; i++) {
    if (!devices[i].active) continue;
    if (topic != String(devices[i].cmdTopic)) continue;
    strncpy(devices[i].state, payload.c_str(), DEV_STATE_LEN - 1);
    devices[i].state[DEV_STATE_LEN - 1] = '\0';
    break;
  }

  Serial.printf("[CMD] %s -> %s\n", topic.c_str(), json.c_str());
  server.send(200, "application/json", "{\"ok\":true}");
}

void handleDashboard() {
  // Send the static HTML shell with a few baked-in fields.
  // Dynamic data (devices, messages, states) is fetched by JS via /api/data.
  String html = FPSTR(DASHBOARD_HTML);
  html.replace("__WIFI_SSID__", creds.wifiSSID);
  html.replace("__IP__",        WiFi.localIP().toString());
  html.replace("__BROKER__",    creds.mqttBroker);
  html.replace("__PORT__",      String(creds.mqttPort));
  html.replace("__EMAIL__",     creds.mqttEmail);
  server.sendHeader("Cache-Control", "no-cache");
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

// ---------------------------------------------------------------------------
//  Parse a homeassistant /config message and register the device
// ---------------------------------------------------------------------------
void parseConfigMsg(const String &topic, const String &payload) {
  // topic format: homeassistant/<type>/<device_id>/<entity_id>/config
  int s1 = topic.indexOf('/');
  if (s1 < 0) return;
  int s2 = topic.indexOf('/', s1 + 1);
  if (s2 < 0) return;
  String entityType = topic.substring(s1 + 1, s2);

  // Only handle controllable / displayable types
  if (entityType != "switch" && entityType != "light" &&
      entityType != "sensor"  && entityType != "binary_sensor" &&
      entityType != "climate" && entityType != "cover" &&
      entityType != "fan"     && entityType != "lock") return;

  JsonDocument doc;
  if (deserializeJson(doc, payload) != DeserializationError::Ok) return;

  const char *name       = doc["name"]          | "";
  const char *stateTopic = doc["state_topic"]   | "";
  const char *cmdTopic   = doc["command_topic"] | "";
  if (strlen(stateTopic) == 0) return;

  // Find existing slot or allocate new one
  int slot = -1;
  for (int i = 0; i < deviceCount; i++) {
    if (devices[i].active && strncmp(devices[i].stateTopic, stateTopic, DEV_TOPIC_LEN) == 0) {
      slot = i; break;
    }
  }
  if (slot < 0 && deviceCount < MAX_DEVICES) slot = deviceCount++;
  if (slot < 0) return;

  devices[slot].active = true;
  strncpy(devices[slot].name,       strlen(name) > 0 ? name : "Unknown", DEV_NAME_LEN - 1);
  strncpy(devices[slot].type,       entityType.c_str(),                   DEV_TYPE_LEN - 1);
  strncpy(devices[slot].stateTopic, stateTopic,                            DEV_TOPIC_LEN - 1);
  strncpy(devices[slot].cmdTopic,   cmdTopic,                              DEV_TOPIC_LEN - 1);
  devices[slot].name[DEV_NAME_LEN - 1]       = '\0';
  devices[slot].type[DEV_TYPE_LEN - 1]       = '\0';
  devices[slot].stateTopic[DEV_TOPIC_LEN - 1] = '\0';
  devices[slot].cmdTopic[DEV_TOPIC_LEN - 1]   = '\0';

  if (mqttClient.connected()) {
    mqttClient.subscribe(devices[slot].stateTopic);
    if (strlen(devices[slot].cmdTopic) > 0) {
      mqttClient.subscribe(devices[slot].cmdTopic);
    }
  }

  Serial.printf("[DEV] Registered: %s (%s)\n", devices[slot].name, devices[slot].type);
}

// ---------------------------------------------------------------------------
//  Update device state when a state message arrives
// ---------------------------------------------------------------------------
void updateDeviceState(const String &topic, const String &payload) {
  for (int i = 0; i < deviceCount; i++) {
    if (!devices[i].active) continue;
    if (topic != String(devices[i].stateTopic)) continue;

    String stateVal = payload;
    JsonDocument doc;
    if (deserializeJson(doc, payload) == DeserializationError::Ok) {
      if (doc.containsKey("state"))       stateVal = doc["state"].as<String>();
      else if (doc.containsKey("temperature")) stateVal = String(doc["temperature"].as<float>(), 1) + " C";
      else if (doc.containsKey("humidity"))    stateVal = String(doc["humidity"].as<float>(), 1) + " %";
      else if (doc.containsKey("value"))       stateVal = doc["value"].as<String>();
    }
    stateVal = stateVal.substring(0, DEV_STATE_LEN - 1);
    strncpy(devices[i].state, stateVal.c_str(), DEV_STATE_LEN - 1);
    devices[i].state[DEV_STATE_LEN - 1] = '\0';
    break;
  }
}

// ---------------------------------------------------------------------------
//  Build HTML for all discovered device cards
// ---------------------------------------------------------------------------
String buildDevicesHtml() {
  if (deviceCount == 0) {
    return "<p style='color:#64748b;font-size:.8rem'>No devices discovered yet. Waiting for MQTT config messages...</p>";
  }
  String html;
  for (int i = 0; i < deviceCount; i++) {
    if (!devices[i].active) continue;
    String type  = String(devices[i].type);
    String state = String(devices[i].state);
    bool isOn    = (state == "ON" || state == "on" || state == "1" || state == "true");
    bool canCtrl = (strlen(devices[i].cmdTopic) > 0) &&
                   (type == "switch" || type == "light" || type == "lock" ||
                    type == "fan"    || type == "cover");

    html += "<div class='dev-card'>";
    html += "<div class='dev-type-lbl'>" + type + "</div>";
    html += "<div class='dev-name'>" + String(devices[i].name) + "</div>";

    if (type == "sensor" || type == "binary_sensor") {
      html += "<div class='dev-val'>" + (state.isEmpty() ? "--" : state) + "</div>";
    } else {
      html += "<span class='badge " + String(isOn ? "on" : (state.isEmpty() ? "warn" : "off")) + "'>";
      html += state.isEmpty() ? "?" : state;
      html += "</span>";
      if (canCtrl) {
        String nextState = isOn ? "OFF" : "ON";
        String cmdTopic  = String(devices[i].cmdTopic);
        html += "<button class='dev-btn' onclick=\"sendCmd('" + cmdTopic + "','" + nextState + "')\">"
                + (isOn ? "Turn OFF" : "Turn ON") + "</button>";
      }
    }
    html += "</div>";
  }
  return html;
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

  // Device discovery from config messages
  if (t.endsWith("/config")) {
    parseConfigMsg(t, p);
  } else {
    // Try to update state for any registered device
    updateDeviceState(t, p);
  }
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

  // Use explicit sub-topic patterns — broker ACL rejects bare # wildcard.
  // Mirrors exactly what app.py subscribes to (which is proven to work).
  const char *components[] = {
    "sensor", "binary_sensor", "switch", "light", "climate", "cover",
    "fan", "lock", "button", "number", "select", "text", "vacuum",
    "alarm_control_panel", "camera", "humidifier", "update", "valve",
    "water_heater", "lawn_mower", "siren", "scene", "event", "notify",
    "device_tracker", "device_automation", "image", nullptr
  };

  String prefix = creds.discoveryPrefix;
  for (int i = 0; components[i] != nullptr; i++) {
    String base = prefix + "/" + components[i] + "/+/+/";
    mqttClient.subscribe((base + "config").c_str());
    mqttClient.subscribe((base + "state").c_str());
    mqttClient.subscribe((base + "set").c_str());
    mqttClient.subscribe((base + "availability").c_str());
  }
  mqttClient.subscribe((prefix + "/device/+/+/config").c_str());
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
  server.on("/reset",     HTTP_GET,  handleReset);
  server.on("/cmd",       HTTP_GET,  handleCmd);
  server.on("/api/data",  HTTP_GET,  handleApiData);
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

// ---------------------------------------------------------------------------
//  Built-in LED status indicator (non-blocking)
//  Active LOW: digitalWrite LOW = LED on, HIGH = LED off
//  AP mode      : fast blink 150ms
//  Wi-Fi only   : slow blink 1000ms
//  Wi-Fi + MQTT : solid ON
// ---------------------------------------------------------------------------
void updateLed() {
  static unsigned long lastToggle = 0;
  static bool ledState = false;
  unsigned long now = millis();

  if (appMode == MODE_SETUP) {
    // Fast blink — provisioning
    if (now - lastToggle >= 150) {
      lastToggle = now;
      ledState = !ledState;
      digitalWrite(STATUS_LED_PIN, ledState ? LOW : HIGH);
    }
  } else if (mqttConnected) {
    // Solid ON
    digitalWrite(STATUS_LED_PIN, LOW);
  } else {
    // Slow blink — Wi-Fi ok but MQTT not connected
    if (now - lastToggle >= 1000) {
      lastToggle = now;
      ledState = !ledState;
      digitalWrite(STATUS_LED_PIN, ledState ? LOW : HIGH);
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);
  startupMs = millis();

  Serial.println("\n=== Nivixsa IoT Dashboard ESP8266 ===");
  pinMode(RESET_BTN_PIN, INPUT_PULLUP);
  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, HIGH); // OFF initially (active LOW)

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
  updateLed();

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
