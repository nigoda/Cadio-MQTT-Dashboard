import requests
import json
import logging
import os
import threading
from datetime import datetime, timedelta
import warnings

# Suppress noisy Google SDK warnings about Python 3.9 EOL
warnings.filterwarnings("ignore", category=FutureWarning, module="google.auth")
warnings.filterwarnings("ignore", category=FutureWarning, module="google.oauth2")
warnings.filterwarnings("ignore", category=FutureWarning, module="google.api_core")

# Default Location
DEFAULT_LAT = 12.840675735693322
DEFAULT_LON = 77.67727845265588

# Setup Gemini API
from dotenv import load_dotenv
from google import genai

import db

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Global Client Instance
_genai_client = None
_current_api_key = None

def refresh_client(user_email=None):
    """Initializes or re-initializes the Gemini client based on DB settings."""
    global _genai_client, _current_api_key
    
    # Try to get settings from DB for the current user
    settings = {"api_mode": "default", "custom_api_key": ""}
    if user_email:
        settings = db.get_api_settings(user_email)
    else:
        # Try last logged in user
        last_user = db.get_last_user()
        if last_user:
            settings = db.get_api_settings(last_user["email"])
    
    if settings.get("api_mode") == "custom" and settings.get("custom_api_key"):
        api_key = settings["custom_api_key"]
    else:
        api_key = GEMINI_API_KEY

    if not api_key:
        _genai_client = None
        _current_api_key = None
        logging.warning("No Gemini API key found (Default or Custom). AI disabled.")
        return False

    if api_key == _current_api_key and _genai_client is not None:
        return True # Already initialized with this key

    try:
        _genai_client = genai.Client(api_key=api_key)
        _current_api_key = api_key
        logging.info(f"Gemini Client initialized using {'CUSTOM' if settings.get('api_mode') == 'custom' else 'DEFAULT'} key.")
        return True
    except Exception as e:
        logging.error(f"Failed to initialize Gemini Client: {e}")
        _genai_client = None
        _current_api_key = None
        return False

# Initial load handled by app.py's _preload_ai_agent

def is_model_loading():
    """Gemini API doesn't need loading, always returns False."""
    return False

def is_model_loaded():
    """Returns True if the API key is configured and client is ready."""
    return _genai_client is not None

def get_weather_data(lat=DEFAULT_LAT, lon=DEFAULT_LON, past_days=7):
    """Fetches past weather + 7-day forecast from Open-Meteo (No API Key required).
    Returns a dict with 'today', 'past' (last N days), and 'forecast' (next 7 days)."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": [
            "temperature_2m_max", 
            "temperature_2m_min",
            "precipitation_sum", 
            "precipitation_probability_max",
            "et0_fao_evapotranspiration",
            "windspeed_10m_max"
        ],
        "timezone": "auto",
        "past_days": past_days
    }

    session = requests.Session()
    retry_strategy = requests.adapters.Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False
    )
    adapter = requests.adapters.HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    try:
        response = session.get(url, params=params, timeout=(5, 20), verify=True)
        response.raise_for_status()
        data = response.json()
        
        daily = data.get("daily", {})
        times = daily.get("time", [])
        temps_max = daily.get("temperature_2m_max", [])
        temps_min = daily.get("temperature_2m_min", [])
        precip = daily.get("precipitation_sum", [])
        prob = daily.get("precipitation_probability_max", [])
        et0 = daily.get("et0_fao_evapotranspiration", [])
        wind = daily.get("windspeed_10m_max", [])
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        past = {}
        forecast = {}
        today_data = None
        
        for i in range(len(times)):
            date_str = times[i]
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            day_name = dt.strftime("%a")
            
            entry = {
                "date": date_str,
                "max_temp_c": temps_max[i] if i < len(temps_max) else None,
                "min_temp_c": temps_min[i] if i < len(temps_min) else None,
                "rain_mm": precip[i] if i < len(precip) else None,
                "rain_prob_pct": prob[i] if i < len(prob) else None,
                "et0_mm": et0[i] if i < len(et0) else None,
                "max_wind_kmh": wind[i] if i < len(wind) else None
            }
            
            if date_str == today_str:
                today_data = entry
                today_data["day"] = day_name
            elif date_str < today_str:
                past[f"{day_name} ({date_str})"] = entry
            else:
                forecast[f"{day_name} ({date_str})"] = entry
                
        return {
            "today": today_data,
            "past_days": past,
            "forecast": forecast
        }
    except requests.exceptions.Timeout as e:
        logging.error(f"Weather fetch timed out after 20s: {e}")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch weather data from Open-Meteo: {e}")
        return None
    finally:
        session.close()

# Backward-compatible alias
def get_7_day_forecast(lat=DEFAULT_LAT, lon=DEFAULT_LON):
    """Backward-compatible wrapper that returns just the forecast portion."""
    result = get_weather_data(lat, lon)
    if not result:
        return None
    # Merge today + forecast for backward compat
    combined = {}
    if result.get("today"):
        day_name = result["today"].get("day", "Today")
        combined[day_name] = result["today"]
    combined.update(result.get("forecast", {}))
    return combined

def build_automation_context(auto_id, auto_data):
    """Extracts relevant info from an automation config for the AI."""
    total_duration = 0
    for action in auto_data.get("actions", []):
        try:
            total_duration += int(action.get("duration", 0))
        except ValueError:
            pass

    sched = auto_data.get("schedule", {})
    is_24hr = sched.get("is24hr", False)

    # Build time ranges string for the AI
    time_ranges = sched.get("timeRanges", [])
    if not time_ranges:
        # Fallback for old single-range format
        s = sched.get("startTime", "")
        e = sched.get("endTime", "")
        if s or e:
            time_ranges = [{"start": s or "00:00", "end": e or "23:59"}]

    if is_24hr:
        time_range_str = "24-Hour Active (no time restriction)"
    elif time_ranges:
        time_range_str = ", ".join(
            f'{r.get("start", "00:00")} to {r.get("end", "23:59")}' for r in time_ranges
        )
    else:
        time_range_str = "00:00 to 23:59"
    # Get last irrigation data from runtime (in-memory only)
    rt = auto_data.get("runtime", {})
    last_irrigated = rt.get("last_irrigated", "Unknown (no data this session)")
    cycles_today = rt.get("cycles_today", 0) if rt.get("cycles_date") == datetime.now().strftime("%Y-%m-%d") else 0
    cycles_history = rt.get("cycles_history", {})

    return {
        "automation_id": auto_id,
        "name": auto_data.get("name", "Unknown"),
        "description": auto_data.get("description", ""),
        "time_ranges": time_range_str,
        "total_water_duration_seconds": total_duration,
        "is_24hr_active": is_24hr,
        "last_irrigated": last_irrigated,
        "cycles_completed_today": cycles_today,
        "max_cycles_per_day": auto_data.get("maxCyclesPerDay", 0),
        "irrigation_history": cycles_history,
        "lat": sched.get("lat"),
        "lon": sched.get("lon")
    }

def get_ai_schedule_decision(weather_data, auto_context, timeout=60):
    """Executes the local AI model to get a scheduling decision.
    weather_data: dict with 'today', 'past_days', and 'forecast' keys."""
    import threading


    # Extract the sections
    today = weather_data.get("today", {})
    past = weather_data.get("past_days", {})
    forecast = weather_data.get("forecast", {})

    today_str = today.get("date", datetime.now().strftime("%Y-%m-%d"))
    today_day = today.get("day", datetime.now().strftime("%a"))

    system_prompt = "You are an expert Agronomist AI that decides optimal irrigation schedules. Analyze weather data and output ONLY valid raw JSON."
    user_prompt = f"""TODAY is {today_day}, {today_str}.
Decide the optimal days to run irrigation for the UPCOMING 7 days based on ALL the data below.

RULES:
1. Do NOT schedule irrigation on days with heavy rain (> 5mm precipitation) or high probability of rain.
2. Prioritize irrigation on or before days with high Evapotranspiration (ET0 > 4.0mm) and high temperatures (> 30°C) as plants lose moisture rapidly.
3. Consider PAST 7 DAYS weather: If recent rain exceeded recent ET0 (water deficit is negative), the soil is still moist — skip early days.
4. Consider Windspeed: High wind (> 20km/h) accelerates ET0 and drying.
5. Check "last_irrigated" in the Automation Details. If within 1 day, you may skip today.
6. Check "irrigation_history" — this shows how many watering cycles ran on each past day.
7. Check "cycles_completed_today" — if already > 0, the system has watered today.
8. Check "max_cycles_per_day" — if this is 0, it means the system will run INFINITE cycles.
9. Select between 1 and 4 days from the upcoming forecast to maintain a healthy soil moisture balance.
10. Output ONLY a raw JSON object (no markdown, no code fences, no conversational text) with exactly these keys:

{{
    "selected_dates": ["YYYY-MM-DD", "YYYY-MM-DD"],
    "reasoning": "Your analysis comparing ET0 against precipitation and irrigation history."
}}

IMPORTANT: In selected_dates, replace YYYY-MM-DD with ONLY full ISO dates chosen from the forecast.

AUTOMATION DETAILS:
{json.dumps(auto_context, indent=2)}

PAST 7 DAYS (actual weather that already happened):
{json.dumps(past, indent=2)}

TODAY ({today_day}, {today_str}):
{json.dumps(today, indent=2)}

UPCOMING 7-DAY FORECAST:
{json.dumps(forecast, indent=2)}"""
    
    if not _genai_client:
        refresh_client()
        
    if not _genai_client:
        logging.error("Cannot run AI scheduling: Gemini Client is not initialized.")
        return None

    try:
        # Combine system and user prompt for Gemini
        full_prompt = f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\nUSER REQUEST:\n{user_prompt}"
        
        response = _genai_client.models.generate_content(
            model="gemini-flash-latest",
            contents=full_prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.1
            }
        )
        
        raw_text = response.text.strip()
        decision = json.loads(raw_text)
        
        if not decision or "selected_dates" not in decision or "reasoning" not in decision:
            logging.error(f"AI missing keys in response: {decision}")
            return None
            
        # Accept the dates exactly as returned
        cleaned_dates = decision["selected_dates"]
        
        # Backward compatibility for the rest of the app: 
        # Convert dates back to short day names for the "selected_days" key
        selected_days = []
        for d_str in cleaned_dates:
            try:
                dt = datetime.strptime(d_str, "%Y-%m-%d")
                selected_days.append(dt.strftime("%a"))
            except:
                continue
                
        decision["selected_days"] = selected_days
        decision["selected_dates"] = cleaned_dates # Keep dates for precision
        
        logging.info(f"Gemini API decision accepted for dates: {cleaned_dates}")
        return decision
        
    except Exception as e:
        logging.error(f"Gemini API failed for automation {auto_context.get('automation_id')}: {e}")
        raise e

# --- FOR TESTING PURPOSES ---
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Fetching weather...")
    forecast = get_7_day_forecast()
    print(json.dumps(forecast, indent=2))
    
    if forecast:
        print("\nAsking local AI for decision (Ensure model is downloaded!)...")
        dummy_auto = {
            "automation_id": "test_auto",
            "name": "Garden Zone 1",
            "schedule": {
                "timeRanges": [
                    {"start": "05:00", "end": "07:00"},
                    {"start": "18:00", "end": "19:00"}
                ],
                "is24hr": False
            },
            "actions": [{"duration": 1200}]
        }
        ctx = build_automation_context("test_auto", dummy_auto)
        decision = get_ai_schedule_decision(forecast, ctx)
        if decision:
            print("\nAI Decision:")
            print(json.dumps(decision, indent=2))
