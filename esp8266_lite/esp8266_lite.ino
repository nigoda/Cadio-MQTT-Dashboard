// =============================================================================
//  esp8266_lite.ino — Nivixsa IoT Dashboard (Lite)
//
//  KEY DESIGN RULES FOR STABILITY:
//  1. Dashboard HTML served via send_P() — ZERO heap allocation
//  2. /api/data uses pre-reserved String — no temporary String objects
//  3. Only devices matching configured serial number are stored
//  4. All char arrays, no String members in device struct
//  5. yield() in every loop iteration to prevent WDT reset
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
#include "pages.h"

// Forward declarations
void jsonStr(String &j, const char *s);
void jsonInt(String &j, int v);

// ---------------------------------------------------------------------------
//  Global state
// ---------------------------------------------------------------------------
CredentialStore credStore;
AppCredentials  creds;

ESP8266WebServer server(SERVER_PORT);
DNSServer dnsServer;

WiFiClient mqttWifi;
PubSubClient mqtt(mqttWifi);

enum Mode { MODE_SETUP, MODE_NORMAL };
Mode mode = MODE_SETUP;

bool mqttConnected = false;
bool pinUnlocked   = false;
unsigned long msgCount     = 0;
unsigned long lastMqttRetry = 0;
unsigned long startupMs    = 0;

// Message ring buffer — fixed-size char arrays, no heap Strings
struct Msg {
  char topic[52];
  char payload[42];
};
Msg msgBuf[MSG_BUF_SIZE];
int msgHead = 0;
bool msgBufEmpty = true;

// ---------------------------------------------------------------------------
//  Device storage — only devices matching creds.unitSerial are kept
// ---------------------------------------------------------------------------
struct Device {
  bool active;
  bool hasBrightness;
  bool hasRgb;
  int  brightness;     // 0-100, -1 unknown
  int  colorR, colorG, colorB;  // 0-255, -1 unknown
  char name[48];
  char deviceId[48];
  char serialId[48];
  char type[16];
  char stateTopic[96];
  char cmdTopic[96];
  char state[48];
  char valueKey[20];
};

Device devices[MAX_DEVICES];
int devCount = 0;

// Unit name (from device.name in config)
char unitName[32] = {0};

// ---------------------------------------------------------------------------
//  Helpers
// ---------------------------------------------------------------------------
String chipID() {
  char id[9];
  snprintf(id, sizeof(id), "%08X", ESP.getChipId());
  return String(id);
}

String uptimeStr() {
  unsigned long s = (millis() - startupMs) / 1000;
  unsigned long m = s / 60; s %= 60;
  unsigned long h = m / 60; m %= 60;
  char buf[20];
  snprintf(buf, sizeof(buf), "%02luh %02lum %02lus", h, m, s);
  return String(buf);
}

void pushMsg(const char *topic, const char *payload) {
  strncpy(msgBuf[msgHead].topic, topic, 51);
  msgBuf[msgHead].topic[51] = '\0';
  strncpy(msgBuf[msgHead].payload, payload, 41);
  msgBuf[msgHead].payload[41] = '\0';
  msgHead = (msgHead + 1) % MSG_BUF_SIZE;
  msgBufEmpty = false;
  msgCount++;
}

// Check if a serialId matches our configured unit serial.
// serialId can be: "A4CF12F03246_0", "A4CF12F03246_LINE_1", etc.
// We check if serialId starts with our configured serial (case-insensitive).
bool serialMatches(const char *serialId) {
  if (creds.unitSerial.length() == 0) return true; // no filter = show all
  return strncasecmp(serialId, creds.unitSerial.c_str(), creds.unitSerial.length()) == 0;
}

// ---------------------------------------------------------------------------
//  JSON builder helpers — zero temporary String allocations
// ---------------------------------------------------------------------------
void jsonStr(String &j, const char *s) {
  while (*s) {
    char c = *s++;
    if (c == '"') j += '\'';
    else if (c == '\\') j += '/';
    else j += c;
  }
}
void jsonInt(String &j, int v) {
  char b[12];
  itoa(v, b, 10);
  j += b;
}

// ---------------------------------------------------------------------------
//  HTTP handlers
// ---------------------------------------------------------------------------
void handleSetupPage() {
  pinUnlocked = false;
  server.send_P(200, "text/html", SETUP_HTML);
}

void handleScan() {
  int n = WiFi.scanNetworks(false, false);
  // Build JSON with reserve to avoid fragmentation
  String json;
  json.reserve(512);
  json = "[";
  for (int i = 0; i < n; i++) {
    if (i) json += ',';
    json += "{\"ssid\":\"";
    jsonStr(json, WiFi.SSID(i).c_str());
    json += "\",\"rssi\":";
    jsonInt(json, WiFi.RSSI(i));
    json += ",\"enc\":";
    json += (WiFi.encryptionType(i) != ENC_TYPE_NONE) ? "true" : "false";
    json += '}';
  }
  json += ']';
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
  if (deserializeJson(doc, server.arg("plain"))) {
    server.send(400, "application/json", "{\"ok\":false,\"msg\":\"JSON error\"}");
    return;
  }

  String ssid   = doc["ssid"]   | "";
  String wpass  = doc["wpass"]  | "";
  String email  = doc["email"]  | "";
  String mpass  = doc["mpass"]  | "";
  String serial = doc["serial"] | "";
  String newpin = doc["newpin"] | "";

  if (ssid.isEmpty() || email.isEmpty() || mpass.isEmpty() || serial.isEmpty()) {
    server.send(400, "application/json", "{\"ok\":false,\"msg\":\"All fields required\"}");
    return;
  }

  credStore.saveAll(ssid, wpass, email, mpass, serial, newpin);
  server.send(200, "application/json", "{\"ok\":true}");
  delay(500);
  ESP.restart();
}

void handleDashboard() {
  // ZERO heap allocation — streams directly from flash (PROGMEM)
  server.sendHeader("Cache-Control", "no-cache");
  server.send_P(200, "text/html", DASHBOARD_HTML);
}

void handleApiData() {
  String json;
  json.reserve(2048);

  json = "{\"ssid\":\"";
  jsonStr(json, creds.wifiSSID.c_str());
  json += "\",\"ip\":\"";
  jsonStr(json, WiFi.localIP().toString().c_str());
  json += "\",\"broker\":\"";
  jsonStr(json, creds.mqttBroker.c_str());
  json += "\",\"port\":";
  jsonInt(json, creds.mqttPort);
  json += ",\"serial\":\"";
  jsonStr(json, creds.unitSerial.c_str());
  json += "\",\"rssi\":";
  jsonInt(json, WiFi.RSSI());
  json += ",\"heap\":";
  jsonInt(json, ESP.getFreeHeap() / 1024);
  json += ",\"uptime\":\"";
  json += uptimeStr();
  json += "\",\"mqtt\":";
  json += mqttConnected ? "true" : "false";
  json += ",\"msg_count\":";
  jsonInt(json, msgCount);

  // Messages
  json += ",\"messages\":[";
  bool first = true;
  if (!msgBufEmpty) {
    for (int i = 0; i < MSG_BUF_SIZE; i++) {
      int idx = ((msgHead - 1 - i) + MSG_BUF_SIZE) % MSG_BUF_SIZE;
      if (msgBuf[idx].topic[0] == '\0') continue;
      if (!first) json += ',';
      first = false;
      json += "{\"t\":\"";
      jsonStr(json, msgBuf[idx].topic);
      json += "\",\"p\":\"";
      jsonStr(json, msgBuf[idx].payload);
      json += "\"}";
    }
  }

  // Devices
  json += "],\"devices\":[";
  first = true;
  for (int i = 0; i < devCount; i++) {
    if (!devices[i].active) continue;
    if (!first) json += ',';
    first = false;

    json += "{\"id\":\"";          jsonStr(json, devices[i].stateTopic);
    json += "\",\"name\":\"";      jsonStr(json, devices[i].name);
    json += "\",\"type\":\"";      jsonStr(json, devices[i].type);
    json += "\",\"state\":\"";     jsonStr(json, devices[i].state);
    json += "\",\"cmd\":\"";       jsonStr(json, devices[i].cmdTopic);
    json += "\",\"supports_brightness\":";
    json += devices[i].hasBrightness ? "true" : "false";
    json += ",\"supports_rgb\":";
    json += devices[i].hasRgb ? "true" : "false";
    json += ",\"brightness\":";    jsonInt(json, devices[i].brightness);
    json += ",\"color_r\":";       jsonInt(json, devices[i].colorR);
    json += ",\"color_g\":";       jsonInt(json, devices[i].colorG);
    json += ",\"color_b\":";       jsonInt(json, devices[i].colorB);
    json += '}';
    yield();
  }
  json += "]}";

  server.sendHeader("Cache-Control", "no-cache");
  server.send(200, "application/json", json);
}

void handleCmd() {
  if (!server.hasArg("topic") || !server.hasArg("payload")) {
    server.send(400, "application/json", "{\"ok\":false,\"msg\":\"Missing args\"}");
    return;
  }
  if (!mqtt.connected()) {
    server.send(503, "application/json", "{\"ok\":false,\"msg\":\"MQTT down\"}");
    return;
  }
  String topic   = server.arg("topic");
  String payload = server.arg("payload");
  bool raw = server.hasArg("raw") && server.arg("raw") == "1";
  String mqttPayload = raw ? payload : ("{\"state\":\"" + payload + "\"}");

  mqtt.publish(topic.c_str(), mqttPayload.c_str(), false);

  // Update local cache immediately
  for (int i = 0; i < devCount; i++) {
    if (!devices[i].active) continue;
    if (topic != String(devices[i].cmdTopic)) continue;

    if (!raw) {
      strncpy(devices[i].state, payload.c_str(), 47);
      devices[i].state[47] = '\0';
      break;
    }
    JsonDocument cmdDoc;
    if (deserializeJson(cmdDoc, payload) == DeserializationError::Ok) {
      if (cmdDoc.containsKey("state")) {
        strncpy(devices[i].state, cmdDoc["state"].as<const char*>(), 47);
        devices[i].state[47] = '\0';
      }
      if (cmdDoc.containsKey("brightness"))
        devices[i].brightness = constrain(cmdDoc["brightness"].as<int>(), 0, 100);
      if (cmdDoc["color"].is<JsonObject>()) {
        JsonObject c = cmdDoc["color"].as<JsonObject>();
        if (c.containsKey("r")) devices[i].colorR = constrain(c["r"].as<int>(), 0, 255);
        if (c.containsKey("g")) devices[i].colorG = constrain(c["g"].as<int>(), 0, 255);
        if (c.containsKey("b")) devices[i].colorB = constrain(c["b"].as<int>(), 0, 255);
      }
    }
    break;
  }
  server.send(200, "application/json", "{\"ok\":true}");
}

void handleReset() {
  credStore.clear();
  server.send(200, "text/html", "<html><body style='font-family:sans-serif;padding:20px'>Reset done. Rebooting...</body></html>");
  delay(1000);
  ESP.restart();
}

void handleCaptiveRedirect() {
  server.sendHeader("Location", "http://192.168.4.1/", true);
  server.send(302, "text/plain", "");
}

// ---------------------------------------------------------------------------
//  Cloud API — get broker info
// ---------------------------------------------------------------------------
bool callLoginApi() {
  Serial.println("[API] Login...");
  BearSSL::WiFiClientSecure sec;
  sec.setInsecure();

  HTTPClient https;
  if (!https.begin(sec, CADIO_LOGIN_URL)) {
    Serial.println("[API] HTTPS begin failed");
    return false;
  }
  https.addHeader("Content-Type", "application/json");

  JsonDocument req;
  req["email"]    = creds.mqttEmail;
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
  if (deserializeJson(resp, response)) {
    Serial.println("[API] JSON parse error");
    return false;
  }

  const char *broker = resp["broker"] | resp["mqtt_broker"] | DEFAULT_MQTT_BROKER;
  int port           = resp["port"]   | resp["mqtt_port"]   | DEFAULT_MQTT_PORT;
  const char *prefix = resp["discovery_prefix"]              | DISCOVERY_PREFIX;

  creds.mqttBroker      = String(broker);
  creds.mqttPort        = port;
  creds.discoveryPrefix = String(prefix);
  credStore.saveBrokerInfo(creds.mqttBroker, creds.mqttPort, creds.discoveryPrefix);

  Serial.printf("[API] Broker: %s:%d\n", broker, port);
  return true;
}

// ---------------------------------------------------------------------------
//  MQTT device discovery — ONLY register devices matching our serial
// ---------------------------------------------------------------------------
void parseConfig(const String &topic, const String &payload) {
  // topic: homeassistant/<type>/<deviceId>/<serialId>/config
  int s1 = topic.indexOf('/');          if (s1 < 0) return;
  int s2 = topic.indexOf('/', s1 + 1); if (s2 < 0) return;
  int s3 = topic.indexOf('/', s2 + 1); if (s3 < 0) return;
  int s4 = topic.indexOf('/', s3 + 1); if (s4 < 0) return;

  String entityType = topic.substring(s1 + 1, s2);
  String deviceId   = topic.substring(s2 + 1, s3);
  String serialId   = topic.substring(s3 + 1, s4);

  // Only accept relevant entity types
  if (entityType != "switch" && entityType != "light" &&
      entityType != "sensor" && entityType != "binary_sensor" &&
      entityType != "climate" && entityType != "cover" &&
      entityType != "fan" && entityType != "lock") return;

  // *** SERIAL FILTER — skip devices not belonging to our unit ***
  if (!serialMatches(serialId.c_str())) {
    Serial.printf("[SKIP] %s (serial: %s)\n", entityType.c_str(), serialId.c_str());
    return;
  }
  Serial.printf("[MATCH] %s (serial: %s)\n", entityType.c_str(), serialId.c_str());

  JsonDocument doc;
  if (deserializeJson(doc, payload)) return;

  const char *name       = doc["name"]          | "";
  const char *stateTopic = doc["state_topic"]   | "";
  const char *cmdTopic   = doc["command_topic"] | "";

  if (strlen(stateTopic) == 0) return;

  // Extract unit name from device object (once)
  if (unitName[0] == '\0') {
    const char *dn = "";
    if (doc["device"].is<JsonObject>()) dn = doc["device"]["name"] | "";
    else if (doc["dev"].is<JsonObject>()) dn = doc["dev"]["name"] | "";
    if (strlen(dn) > 0) {
      strncpy(unitName, dn, 31);
      unitName[31] = '\0';
      Serial.printf("[UNIT] Name: %s\n", unitName);
    }
  }

  // Extract valueKey from value_template
  char valKey[20] = {0};
  const char *vt = doc["value_template"] | "";
  if (strlen(vt) == 0) vt = doc["val_tpl"] | "";
  if (strlen(vt) > 0) {
    const char *dot = strstr(vt, "value_json.");
    if (dot) {
      dot += 11;
      int k = 0;
      while (*dot && *dot != ' ' && *dot != '}' && *dot != '|' && k < 18)
        valKey[k++] = *dot++;
      valKey[k] = '\0';
    }
  }

  // Find existing slot by serialId
  int slot = -1;
  bool existing = false;
  for (int i = 0; i < devCount; i++) {
    if (devices[i].active && strncmp(devices[i].serialId, serialId.c_str(), 47) == 0) {
      slot = i;
      existing = true;
      break;
    }
  }
  if (slot < 0 && devCount < MAX_DEVICES) slot = devCount++;
  if (slot < 0) return;

  // Brightness / RGB support
  bool bri = doc["brightness"] | false;
  bool rgb = false;
  if (doc["supported_color_modes"].is<JsonArray>()) {
    for (JsonVariant v : doc["supported_color_modes"].as<JsonArray>()) {
      String m = v.as<String>();
      if (m == "brightness" || m == "rgb") bri = true;
      if (m == "rgb") rgb = true;
    }
  }

  devices[slot].active        = true;
  devices[slot].hasBrightness = (entityType == "light") && bri;
  devices[slot].hasRgb        = (entityType == "light") && rgb;
  if (!existing) {
    devices[slot].brightness = -1;
    devices[slot].colorR = -1;
    devices[slot].colorG = -1;
    devices[slot].colorB = -1;
    devices[slot].state[0] = '\0';
  }

  strncpy(devices[slot].name,       strlen(name) > 0 ? name : "Unknown", 47);
  strncpy(devices[slot].deviceId,   deviceId.c_str(),  47);
  strncpy(devices[slot].serialId,   serialId.c_str(),  47);
  strncpy(devices[slot].type,       entityType.c_str(), 15);
  strncpy(devices[slot].stateTopic, stateTopic,         95);
  strncpy(devices[slot].cmdTopic,   cmdTopic,           95);
  memcpy(devices[slot].valueKey,    valKey,             20);

  // Null-terminate all
  devices[slot].name[47]       = '\0';
  devices[slot].deviceId[47]   = '\0';
  devices[slot].serialId[47]   = '\0';
  devices[slot].type[15]       = '\0';
  devices[slot].stateTopic[95] = '\0';
  devices[slot].cmdTopic[95]   = '\0';

  // Subscribe to this device's topics
  if (mqtt.connected()) {
    mqtt.subscribe(stateTopic);
    if (strlen(cmdTopic) > 0) mqtt.subscribe(cmdTopic);
  }

  Serial.printf("[DEV] %s: %s (%s)\n", existing ? "Updated" : "Added", devices[slot].name, devices[slot].type);
}

void updateState(const String &topic, const String &payload) {
  JsonDocument doc;
  bool jsonOk = (deserializeJson(doc, payload) == DeserializationError::Ok);

  for (int i = 0; i < devCount; i++) {
    if (!devices[i].active) continue;
    if (topic != String(devices[i].stateTopic)) continue;

    // Extract value
    const char *val = payload.c_str();
    char tmp[48];

    if (jsonOk) {
      if (devices[i].valueKey[0] != '\0') {
        // Specific key for multi-value sensors
        if (doc.containsKey(devices[i].valueKey)) {
          if (doc[devices[i].valueKey].is<float>()) {
            snprintf(tmp, sizeof(tmp), "%.2f", doc[devices[i].valueKey].as<float>());
            val = tmp;
          } else {
            strncpy(tmp, doc[devices[i].valueKey].as<const char*>() ?: "", 47);
            tmp[47] = '\0';
            val = tmp;
          }
        }
      } else {
        if (doc.containsKey("state")) {
          strncpy(tmp, doc["state"].as<const char*>() ?: "", 47);
          tmp[47] = '\0';
          val = tmp;
        } else if (doc.containsKey("temperature")) {
          snprintf(tmp, sizeof(tmp), "%.1f C", doc["temperature"].as<float>());
          val = tmp;
        } else if (doc.containsKey("humidity")) {
          snprintf(tmp, sizeof(tmp), "%.1f %%", doc["humidity"].as<float>());
          val = tmp;
        } else if (doc.containsKey("value")) {
          strncpy(tmp, doc["value"].as<const char*>() ?: "", 47);
          tmp[47] = '\0';
          val = tmp;
        }
      }

      // Brightness & color
      if (doc.containsKey("brightness"))
        devices[i].brightness = constrain(doc["brightness"].as<int>(), 0, 100);
      if (doc["color"].is<JsonObject>()) {
        JsonObject c = doc["color"].as<JsonObject>();
        if (c.containsKey("r")) devices[i].colorR = constrain(c["r"].as<int>(), 0, 255);
        if (c.containsKey("g")) devices[i].colorG = constrain(c["g"].as<int>(), 0, 255);
        if (c.containsKey("b")) devices[i].colorB = constrain(c["b"].as<int>(), 0, 255);
      }
    }

    strncpy(devices[i].state, val, 47);
    devices[i].state[47] = '\0';
    // Don't break — multiple entities may share same state_topic
  }
}

// ---------------------------------------------------------------------------
//  MQTT callback & connection
// ---------------------------------------------------------------------------
void mqttCallback(char *topic, byte *payload, unsigned int length) {
  // Null-terminate payload in place (PubSubClient reserves space for this)
  payload[length] = '\0';
  const char *pStr = (const char *)payload;

  // Push truncated version to message log (display only)
  pushMsg(topic, pStr);

  String t(topic);

  if (t.endsWith("/config")) {
    // Config payloads can be 500-2000 bytes — pass full payload
    String p;
    p.reserve(length + 1);
    p = pStr;
    parseConfig(t, p);
  } else {
    // State payloads are small — use directly
    String p(pStr);
    updateState(t, p);
  }
}

bool connectMqtt() {
  if (mqtt.connected()) return true;

  String clientId = String(MQTT_CLIENT_ID_PREFIX) + chipID();
  mqtt.setServer(creds.mqttBroker.c_str(), creds.mqttPort);
  mqtt.setCallback(mqttCallback);
  mqtt.setKeepAlive(MQTT_KEEPALIVE);
  mqtt.setBufferSize(MQTT_BUFFER_SIZE);

  Serial.printf("[MQTT] Connecting %s:%d...\n", creds.mqttBroker.c_str(), creds.mqttPort);

  bool ok = mqtt.connect(clientId.c_str(), creds.mqttEmail.c_str(), creds.mqttPassword.c_str());
  if (!ok) {
    Serial.printf("[MQTT] Failed rc=%d\n", mqtt.state());
    return false;
  }

  mqttConnected = true;
  Serial.println("[MQTT] Connected");

  // Subscribe to discovery topics
  const char *comps[] = {
    "sensor", "binary_sensor", "switch", "light", "climate",
    "cover", "fan", "lock", NULL
  };

  String prefix = creds.discoveryPrefix;
  for (int i = 0; comps[i]; i++) {
    String base = prefix + "/" + comps[i] + "/+/+/";
    mqtt.subscribe((base + "config").c_str());
    mqtt.subscribe((base + "state").c_str());
    mqtt.subscribe((base + "set").c_str());
    yield();
  }

  // Re-subscribe to already-discovered devices
  for (int i = 0; i < devCount; i++) {
    if (!devices[i].active) continue;
    mqtt.subscribe(devices[i].stateTopic);
    if (devices[i].cmdTopic[0]) mqtt.subscribe(devices[i].cmdTopic);
    yield();
  }

  return true;
}

// ---------------------------------------------------------------------------
//  LED indicator (non-blocking)
// ---------------------------------------------------------------------------
void updateLed() {
  static unsigned long lastToggle = 0;
  static bool ledOn = false;
  unsigned long now = millis();

  if (WiFi.status() != WL_CONNECTED) {
    // Blink fast when disconnected
    if (now - lastToggle >= 250) {
      lastToggle = now;
      ledOn = !ledOn;
      digitalWrite(STATUS_LED_PIN, ledOn ? LOW : HIGH);
    }
    return;
  }
  // Solid ON when connected
  digitalWrite(STATUS_LED_PIN, LOW);
}

// ---------------------------------------------------------------------------
//  Reset button (hold 3s)
// ---------------------------------------------------------------------------
void checkReset() {
  if (digitalRead(RESET_BTN_PIN) != LOW) return;
  unsigned long pressed = millis();
  while (digitalRead(RESET_BTN_PIN) == LOW) {
    if (millis() - pressed >= RESET_HOLD_MS) {
      Serial.println("[BTN] Factory reset!");
      credStore.clear();
      delay(250);
      ESP.restart();
    }
    delay(50);
  }
}

// ---------------------------------------------------------------------------
//  AP mode
// ---------------------------------------------------------------------------
void startAP() {
  Serial.println("[MODE] AP Setup");
  mode = MODE_SETUP;

  WiFi.mode(WIFI_AP);
  WiFi.softAPConfig(AP_IP, AP_GATEWAY, AP_SUBNET);

  String apSSID = String(AP_SSID_PREFIX) + "-" + chipID().substring(4);
  if (strlen(AP_PASSWORD) > 0) WiFi.softAP(apSSID.c_str(), AP_PASSWORD);
  else WiFi.softAP(apSSID.c_str());

  Serial.printf("[AP] SSID: %s  IP: %s\n", apSSID.c_str(), AP_IP.toString().c_str());

  dnsServer.start(DNS_PORT, "*", AP_IP);

  server.on("/",          HTTP_GET,  handleSetupPage);
  server.on("/setup",     HTTP_GET,  handleSetupPage);
  server.on("/scan",      HTTP_GET,  handleScan);
  server.on("/check_pin", HTTP_GET,  handleCheckPin);
  server.on("/save",      HTTP_POST, handleSave);
  server.onNotFound(handleCaptiveRedirect);
  server.begin();
}

void startNormal() {
  Serial.println("[MODE] Normal");
  mode = MODE_NORMAL;

  server.on("/",          HTTP_GET,  handleDashboard);
  server.on("/setup",     HTTP_GET,  handleSetupPage);
  server.on("/scan",      HTTP_GET,  handleScan);
  server.on("/check_pin", HTTP_GET,  handleCheckPin);
  server.on("/save",      HTTP_POST, handleSave);
  server.on("/reset",     HTTP_GET,  handleReset);
  server.on("/cmd",       HTTP_GET,  handleCmd);
  server.on("/api/data",  HTTP_GET,  handleApiData);
  server.onNotFound([]() { server.send(404, "text/plain", "Not found"); });
  server.begin();

  Serial.printf("[WEB] http://%s/\n", WiFi.localIP().toString().c_str());
}

// ---------------------------------------------------------------------------
//  setup() & loop()
// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);
  startupMs = millis();

  Serial.println("\n=== Nivixsa IoT Lite ===");
  pinMode(RESET_BTN_PIN, INPUT_PULLUP);
  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, HIGH); // OFF (active LOW)

  // Clear message buffer
  memset(msgBuf, 0, sizeof(msgBuf));
  memset(devices, 0, sizeof(devices));

  bool hasCreds = credStore.load(creds);
  if (!hasCreds || creds.unitSerial.length() == 0) {
    startAP();
    return;
  }

  Serial.printf("[CFG] Serial filter: %s\n", creds.unitSerial.c_str());

  WiFi.mode(WIFI_STA);
  WiFi.begin(creds.wifiSSID.c_str(), creds.wifiPassword.c_str());

  Serial.printf("[WiFi] Connecting to %s", creds.wifiSSID.c_str());
  unsigned long lastAttempt = millis();
  while (WiFi.status() != WL_CONNECTED) {
    updateLed();
    checkReset();
    if (millis() - lastAttempt > WIFI_CONNECT_TIMEOUT_MS) {
      Serial.println("\n[WiFi] Timeout, retrying...");
      WiFi.disconnect();
      delay(100);
      WiFi.begin(creds.wifiSSID.c_str(), creds.wifiPassword.c_str());
      lastAttempt = millis();
    }
    Serial.print(".");
    delay(250);
  }
  Serial.printf("\n[WiFi] Connected. IP: %s\n", WiFi.localIP().toString().c_str());

  if (!callLoginApi()) {
    Serial.println("[API] Failed, using cached broker");
  }

  connectMqtt();
  startNormal();
}

void loop() {
  checkReset();
  updateLed();

  if (mode == MODE_SETUP) {
    dnsServer.processNextRequest();
    server.handleClient();
    return;
  }

  // WiFi reconnect
  if (WiFi.status() != WL_CONNECTED) {
    static unsigned long lastWifiRetry = 0;
    unsigned long now = millis();
    if (now - lastWifiRetry >= MQTT_RECONNECT_INTERVAL_MS) {
      lastWifiRetry = now;
      Serial.println("[WiFi] Reconnecting...");
      WiFi.disconnect();
      WiFi.begin(creds.wifiSSID.c_str(), creds.wifiPassword.c_str());
    }
    server.handleClient();
    return;
  }

  // MQTT reconnect
  if (!mqtt.connected()) {
    mqttConnected = false;
    unsigned long now = millis();
    if (now - lastMqttRetry >= MQTT_RECONNECT_INTERVAL_MS) {
      lastMqttRetry = now;
      connectMqtt();
    }
  }

  mqtt.loop();
  server.handleClient();
  yield();
}
