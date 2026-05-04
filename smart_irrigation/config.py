"""
Smart Irrigation Dashboard — Configuration
"""
import os

# MQTT
CADIO_LOGIN_URL = "https://egycad.com/apis/cadio/login"
MQTT_BROKER = os.getenv("MQTT_BROKER", "egycad.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
DISCOVERY_PREFIX = "homeassistant"

# Engine
VERIFY_TIMEOUT_SEC = 10
MAX_RETRIES = 3
DEFAULT_BUFFER_SEC = 5
ENGINE_TICK_INTERVAL = 0.5  # seconds between engine ticks

# Flask
SECRET_KEY = os.getenv("SECRET_KEY", os.urandom(24))
