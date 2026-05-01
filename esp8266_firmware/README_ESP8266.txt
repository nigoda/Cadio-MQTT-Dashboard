Nivixsa NodeMCU (ESP8266) Firmware
==================================

Files in this folder
--------------------
- esp8266_firmware.ino
- config.h
- credentials.h
- webpages.h

Arduino IDE setup
-----------------
1) File > Preferences > Additional Boards Manager URLs:
   http://arduino.esp8266.com/stable/package_esp8266com_index.json

2) Tools > Board > Boards Manager:
   Install "esp8266" by ESP8266 Community.

3) Select board:
   Tools > Board > ESP8266 Boards > NodeMCU 1.0 (ESP-12E Module)

4) Install libraries from Library Manager:
   - PubSubClient (Nick O'Leary)
   - ArduinoJson (Benoit Blanchon)

5) Open esp8266_firmware.ino and click Upload.

After flashing (first setup)
----------------------------
1) ESP starts AP mode as: Nivixsa-Setup-XXXX
2) Connect phone/laptop to that Wi-Fi
3) Open: http://192.168.4.1
4) Enter setup PIN (default: 1234)
5) Enter:
   - Home Wi-Fi SSID/password
   - Nivixsa MQTT email/password
6) Save and reboot.

Normal usage
------------
- Dashboard is available at: http://<esp-ip>/
- Reconfigure via: http://<esp-ip>/setup
- Factory reset via: http://<esp-ip>/reset

Physical reset button
---------------------
- Uses built-in FLASH button: GPIO0 (NodeMCU D3)
- Hold LOW for 5 seconds to clear saved credentials
- Important: do not keep FLASH pressed during power-on/reset boot
- Edit config.h if your wiring differs
