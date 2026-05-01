// =============================================================================
//  esp32_firmware.ino
//  Nivixsa IoT Dashboard — ESP32 provisioning firmware
//
//  FLOW:
//    1. Read credentials from NVS.
//    2a. No credentials → AP mode (captive portal setup page).
//    2b. Credentials found → connect to Wi-Fi.
//        • Wi-Fi fails for WIFI_CONNECT_TIMEOUT_MS → fall back to AP mode.
//    3. After Wi-Fi: call Nivixsa login API → get MQTT broker details.
//    4. Connect to MQTT, subscribe to homeassistant/# topics.
//    5. Host status dashboard on local LAN IP (port 80).
//    6. Physical reset: hold RESET_BTN_PIN for RESET_HOLD_MS to wipe NVS.
//
//  REQUIRED LIBRARIES  (install via Arduino Library Manager):
//    • PubSubClient   — by Nick O'Leary
//    • ArduinoJson    — by Benoit Blanchon  (v7.x)
//
//  BOARD: "ESP32 Dev Module" (or equivalent) in Arduino IDE / PlatformIO
// =============================================================================

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

#include "config.h"
#include "credentials.h"
#include "webpages.h"

// ---------------------------------------------------------------------------
//  Globals
// ---------------------------------------------------------------------------
CredentialStore credStore;
AppCredentials  creds;

WebServer  server(SERVER_PORT);
DNSServer  dnsServer;

WiFiClientSecure  secureClient;   // for HTTPS login API
WiFiClient        mqttWifiClient; // for MQTT (plain TCP — broker is on port 1883)
PubSubClient      mqttClient(mqttWifiClient);

enum AppMode { MODE_SETUP, MODE_NORMAL };
AppMode appMode = MODE_SETUP;

bool mqttConnected   = false;
bool pinUnlocked     = false;     // set per HTTP session via /check_pin
unsigned long msgCount       = 0;
unsigned long lastMqttRetry  = 0;
unsigned long startupMs      = millis();

// Recent messages ring buffer (last 20)
struct MqttMsg { String topic; String payload; };
#define MSG_BUF_SIZE 20
MqttMsg msgBuf[MSG_BUF_SIZE];
int msgBufHead = 0;

// ---------------------------------------------------------------------------
//  Helpers
// ---------------------------------------------------------------------------
String chipID() {
  uint8_t mac[6];
  WiFi.macAddress(mac);
  char buf[5];
  snprintf(buf, sizeof(buf), "%02X%02X", mac[4], mac[5]);
  return String(buf);
}

String uptimeStr() {
  unsigned long s = (millis() - startupMs) / 1000;
  unsigned long m = s / 60; s %= 60;
  unsigned long h = m / 60; m %= 60;
  char buf[32];
  snprintf(buf, sizeof(buf), "%02luh %02lum %02lus", h, m, s);
  return String(buf);
}

void pushMsg(const String &topic, const String &payload) {
  msgBuf[msgBufHead] = {topic, payload};
  msgBufHead = (msgBufHead + 1) % MSG_BUF_SIZE;
  msgCount++;
}

// ---------------------------------------------------------------------------
//  Captive portal redirect — all DNS queries in AP mode reply with AP_IP
// ---------------------------------------------------------------------------
void startCaptivePortal() {
  dnsServer.start(DNS_PORT, "*", AP_IP);
}

// ---------------------------------------------------------------------------
//  Wi-Fi scan (returns JSON array)
// ---------------------------------------------------------------------------
void handleScan() {
  int n = WiFi.scanNetworks(/*async=*/false, /*show_hidden=*/false);
  String json = "[";
  for (int i = 0; i < n; i++) {
    if (i) json += ",";
    json += "{\"ssid\":\"" + WiFi.SSID(i) + "\","
            "\"rssi\":"    + WiFi.RSSI(i) + ","
            "\"enc\":"     + (WiFi.encryptionType(i) != WIFI_AUTH_OPEN ? "true" : "false") + "}";
  }
  json += "]";
  server.send(200, "application/json", json);
}

// ---------------------------------------------------------------------------
//  PIN check
// ---------------------------------------------------------------------------
void handleCheckPin() {
  String pin = server.arg("pin");
  if (pin == creds.setupPin) {
    pinUnlocked = true;
    server.send(200, "application/json", "{\"ok\":true}");
  } else {
    server.send(200, "application/json", "{\"ok\":false}");
  }
}

// ---------------------------------------------------------------------------
//  Save credentials — POST /save  { ssid, wpass, email, mpass, newpin }
// ---------------------------------------------------------------------------
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

// ---------------------------------------------------------------------------
//  Setup page (AP mode) — GET /  or  GET /setup
// ---------------------------------------------------------------------------
void handleSetupPage() {
  pinUnlocked = false;                         // require PIN on each visit
  credStore.load(creds);                       // refresh so PIN is current
  server.send(200, "text/html", FPSTR(SETUP_HTML));
}

// ---------------------------------------------------------------------------
//  Dashboard page (normal mode) — GET /
// ---------------------------------------------------------------------------
void handleDashboard() {
  // Build recent messages HTML
  String msgs = "";
  for (int i = 0; i < MSG_BUF_SIZE; i++) {
    // Iterate newest → oldest
    int idx = ((msgBufHead - 1 - i) + MSG_BUF_SIZE) % MSG_BUF_SIZE;
    if (msgBuf[idx].topic.isEmpty()) continue;
    msgs += "<div class='msg-item'>"
            "<span class='ts'>" + String(msgCount - i) + "</span>"
            "<span class='topic'>" + msgBuf[idx].topic + "</span> → "
            + msgBuf[idx].payload.substring(0, 120) +
            "</div>";
  }
  if (msgs.isEmpty()) msgs = "<p style='color:#64748b;font-size:.8rem'>No messages yet.</p>";

  String html = FPSTR(DASHBOARD_HTML);
  html.replace("__WIFI_SSID__",   creds.wifiSSID);
  html.replace("__IP__",          WiFi.localIP().toString());
  html.replace("__RSSI__",        String(WiFi.RSSI()));
  html.replace("__BROKER__",      creds.mqttBroker);
  html.replace("__PORT__",        String(creds.mqttPort));
  html.replace("__EMAIL__",       creds.mqttEmail);
  html.replace("__MQTT_CLASS__",  mqttConnected ? "on" : "off");
  html.replace("__MQTT_STATUS__", mqttConnected ? "Connected" : "Disconnected");
  html.replace("__MSG_COUNT__",   String(msgCount));
  html.replace("__UPTIME__",      uptimeStr());
  html.replace("__HEAP__",        String(ESP.getFreeHeap() / 1024));
  html.replace("__MESSAGES__",    msgs);
  server.send(200, "text/html", html);
}

// ---------------------------------------------------------------------------
//  Factory reset via browser — GET /reset
// ---------------------------------------------------------------------------
void handleReset() {
  credStore.clear();
  server.send(200, "text/html",
    "<html><body style='background:#0f172a;color:#e2e8f0;font-family:sans-serif;padding:40px'>"
    "<h2>Credentials wiped. Restarting in setup mode...</h2></body></html>");
  delay(1000);
  ESP.restart();
}

// ---------------------------------------------------------------------------
//  Captive portal catch-all (redirects everything to the setup page)
// ---------------------------------------------------------------------------
void handleCaptivePortalRedirect() {
  server.sendHeader("Location", "http://192.168.4.1/", true);
  server.send(302, "text/plain", "");
}

// ---------------------------------------------------------------------------
//  Nivixsa login API — returns true and populates creds on success
// ---------------------------------------------------------------------------
bool callLoginApi() {
  Serial.println("[API] Calling Nivixsa login API...");

  secureClient.setInsecure();   // Accept any cert — OK for internal IoT provisioning;
                                 // replace with secureClient.setCACert(rootCA) for stricter validation.
  HTTPClient https;
  if (!https.begin(secureClient, CADIO_LOGIN_URL)) {
    Serial.println("[API] Failed to begin HTTPS");
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

  JsonDocument resp;
  DeserializationError err = deserializeJson(resp, https.getString());
  https.end();
  if (err) {
    Serial.println("[API] JSON parse error");
    return false;
  }

  // Extract broker details — adjust key names if the API response differs
  const char *broker = resp["broker"] | resp["mqtt_broker"] | DEFAULT_MQTT_BROKER;
  int   port         = resp["port"]   | resp["mqtt_port"]   | DEFAULT_MQTT_PORT;
  const char *prefix = resp["discovery_prefix"] | DISCOVERY_PREFIX;

  creds.mqttBroker      = String(broker);
  creds.mqttPort        = port;
  creds.discoveryPrefix = String(prefix);
  credStore.saveBrokerInfo(creds.mqttBroker, creds.mqttPort, creds.discoveryPrefix);

  Serial.printf("[API] Broker: %s:%d  prefix: %s\n",
                creds.mqttBroker.c_str(), creds.mqttPort, creds.discoveryPrefix.c_str());
  return true;
}

// ---------------------------------------------------------------------------
//  MQTT callbacks
// ---------------------------------------------------------------------------
void mqttCallback(char *topic, byte *payload, unsigned int length) {
  String t(topic);
  String p;
  p.reserve(length);
  for (unsigned int i = 0; i < length; i++) p += (char)payload[i];
  pushMsg(t, p);
  Serial.printf("[MQTT] %s -> %s\n", t.c_str(), p.substring(0, 80).c_str());
}

bool connectMqtt() {
  if (mqttClient.connected()) return true;

  String clientId = MQTT_CLIENT_ID_PREFIX + chipID();
  Serial.printf("[MQTT] Connecting to %s:%d as %s...\n",
                creds.mqttBroker.c_str(), creds.mqttPort, clientId.c_str());

  mqttClient.setServer(creds.mqttBroker.c_str(), creds.mqttPort);
  mqttClient.setCallback(mqttCallback);
  mqttClient.setKeepAlive(MQTT_KEEPALIVE);
  mqttClient.setBufferSize(MQTT_BUFFER_SIZE);

  bool ok = mqttClient.connect(
    clientId.c_str(),
    creds.mqttEmail.c_str(),
    creds.mqttPassword.c_str()
  );

  if (!ok) {
    Serial.printf("[MQTT] Connect failed, rc=%d\n", mqttClient.state());
    return false;
  }

  Serial.println("[MQTT] Connected.");
  mqttConnected = true;

  // Subscribe to all homeassistant discovery topics (mirrors app.py)
  const char *components[] = {
    "sensor", "binary_sensor", "switch", "light", "climate", "cover",
    "fan", "lock", "button", "number", "select", "text", "vacuum",
    "alarm_control_panel", "camera", "humidifier", "update", "valve",
    "water_heater", "lawn_mower", "siren", "scene", "event", "notify",
    "device_tracker", "device_automation", "image", nullptr
  };
  String prefix = creds.discoveryPrefix;
  for (int i = 0; components[i] != nullptr; i++) {
    String t = prefix + "/" + components[i] + "/+/+/#";
    mqttClient.subscribe(t.c_str());
  }
  mqttClient.subscribe((prefix + "/status").c_str());
  return true;
}

// ---------------------------------------------------------------------------
//  AP mode setup — used on first boot or when Wi-Fi credentials are missing/bad
// ---------------------------------------------------------------------------
void startAPMode() {
  Serial.println("[MODE] Starting AP (setup) mode...");
  appMode = MODE_SETUP;

  WiFi.mode(WIFI_AP);
  WiFi.softAPConfig(AP_IP, AP_GATEWAY, AP_SUBNET);
  String apSSID = String(AP_SSID_PREFIX) + "-" + chipID();
  WiFi.softAP(apSSID.c_str(), (strlen(AP_PASSWORD) > 0) ? AP_PASSWORD : nullptr);

  Serial.printf("[AP] SSID: %s  IP: %s\n", apSSID.c_str(), AP_IP.toString().c_str());

  startCaptivePortal();

  // Web routes in AP mode
  server.on("/",          HTTP_GET,  handleSetupPage);
  server.on("/setup",     HTTP_GET,  handleSetupPage);
  server.on("/scan",      HTTP_GET,  handleScan);
  server.on("/check_pin", HTTP_GET,  handleCheckPin);
  server.on("/save",      HTTP_POST, handleSave);
  server.onNotFound(handleCaptivePortalRedirect);
  server.begin();
}

// ---------------------------------------------------------------------------
//  Normal mode setup — after successful Wi-Fi connection
// ---------------------------------------------------------------------------
void startNormalMode() {
  Serial.println("[MODE] Starting normal mode...");
  appMode = MODE_NORMAL;

  // Route setup in normal mode
  server.on("/",          HTTP_GET, handleDashboard);
  server.on("/setup",     HTTP_GET, handleSetupPage);
  server.on("/scan",      HTTP_GET, handleScan);
  server.on("/check_pin", HTTP_GET, handleCheckPin);
  server.on("/save",      HTTP_POST, handleSave);
  server.on("/reset",     HTTP_GET, handleReset);
  server.onNotFound([](){ server.send(404, "text/plain", "Not found"); });
  server.begin();
  Serial.printf("[WEB] Dashboard at http://%s/\n", WiFi.localIP().toString().c_str());
}

// ---------------------------------------------------------------------------
//  Physical reset button — hold RESET_BTN_PIN for RESET_HOLD_MS
// ---------------------------------------------------------------------------
void checkResetButton() {
  if (digitalRead(RESET_BTN_PIN) != LOW) return;  // button not pressed

  unsigned long pressedAt = millis();
  while (digitalRead(RESET_BTN_PIN) == LOW) {
    if (millis() - pressedAt >= RESET_HOLD_MS) {
      Serial.println("[BTN] Factory reset triggered.");
      credStore.clear();
      delay(200);
      ESP.restart();
    }
    delay(50);
  }
}

// ===========================================================================
//  SETUP
// ===========================================================================
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n=== Nivixsa IoT Dashboard ESP32 ===");

  pinMode(RESET_BTN_PIN, INPUT_PULLUP);

  bool hasCredentials = credStore.load(creds);

  if (!hasCredentials) {
    // ---- First boot or wiped: go to AP provisioning mode ----
    startAPMode();
    return;
  }

  // ---- Try to connect to saved Wi-Fi ----
  Serial.printf("[WiFi] Connecting to \"%s\"...\n", creds.wifiSSID.c_str());
  WiFi.mode(WIFI_STA);
  WiFi.begin(creds.wifiSSID.c_str(), creds.wifiPassword.c_str());

  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - t0 > WIFI_CONNECT_TIMEOUT_MS) {
      Serial.println("[WiFi] Connection timed out. Falling back to AP mode.");
      startAPMode();
      return;
    }
    delay(200);
    Serial.print(".");
  }
  Serial.printf("\n[WiFi] Connected. IP: %s\n", WiFi.localIP().toString().c_str());

  // ---- Call login API to get/refresh MQTT broker details ----
  bool apiOk = callLoginApi();
  if (!apiOk) {
    // Use cached broker from previous successful login
    Serial.println("[API] Using cached broker credentials.");
  }

  // ---- Connect to MQTT ----
  connectMqtt();

  // ---- Start web server ----
  startNormalMode();
}

// ===========================================================================
//  LOOP
// ===========================================================================
void loop() {
  checkResetButton();

  if (appMode == MODE_SETUP) {
    dnsServer.processNextRequest();
    server.handleClient();
    return;
  }

  // Normal mode: keep Wi-Fi alive
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Lost connection. Attempting reconnect...");
    WiFi.reconnect();
    unsigned long t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < WIFI_CONNECT_TIMEOUT_MS) {
      delay(200);
    }
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("[WiFi] Reconnect failed. Restarting into AP mode.");
      delay(500);
      ESP.restart();
    }
  }

  // Keep MQTT alive / reconnect
  if (!mqttClient.connected()) {
    mqttConnected = false;
    unsigned long now = millis();
    if (now - lastMqttRetry > MQTT_RECONNECT_INTERVAL_MS) {
      lastMqttRetry = now;
      connectMqtt();
    }
  }
  mqttClient.loop();

  server.handleClient();
}
