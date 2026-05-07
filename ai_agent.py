import requests
import json
import logging
import os
import threading
from datetime import datetime, timedelta

# Default Location
DEFAULT_LAT = 12.840675735693322
DEFAULT_LON = 77.67727845265588

# Local Model Configuration
# Download a .gguf model (e.g. Llama-3.2-1B-Instruct-Q4_K_M.gguf) and place it here:
MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "llama-3.2-1b-instruct.gguf")

# Global reference to the loaded model so it only loads once into memory
_llm_instance = None
_llm_loading_lock = threading.Lock()
_llm_is_loading = False

def get_llm():
    global _llm_instance, _llm_is_loading
    
    # Fast path: already loaded
    if _llm_instance is not None:
        return _llm_instance
    
    # Slow path: need to load (with lock to prevent double-load)
    with _llm_loading_lock:
        # Double-check after acquiring lock
        if _llm_instance is not None:
            return _llm_instance
            
        try:
            from llama_cpp import Llama
        except ImportError:
            logging.error("Missing dependency! Run: pip install llama-cpp-python")
            return None
            
        if not os.path.exists(MODEL_PATH):
            logging.error(f"Model file not found at {MODEL_PATH}. Please download a .gguf model.")
            return None
        
        _llm_is_loading = True
        logging.info("Loading AI model into memory. This may take a few seconds...")
        
        try:
            # n_ctx is the context window size. 2048 is plenty for our schedule JSON.
            _llm_instance = Llama(model_path=MODEL_PATH, n_ctx=2048, verbose=False)
            logging.info("AI model loaded successfully!")
        finally:
            _llm_is_loading = False
        
    return _llm_instance

def is_model_loading():
    """Check if model is currently loading (for UI status updates)."""
    return _llm_is_loading

def is_model_loaded():
    """Check if model is loaded and ready."""
    return _llm_instance is not None

def get_weather_data(lat=DEFAULT_LAT, lon=DEFAULT_LON, past_days=3):
    """Fetches past weather + 7-day forecast from Open-Meteo (No API Key required).
    Returns a dict with 'today', 'past' (last N days), and 'forecast' (next 7 days)."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": ["temperature_2m_max", "precipitation_sum", "precipitation_probability_max"],
        "timezone": "auto",
        "past_days": past_days
    }
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    try:
        response = requests.get(url, params=params, timeout=10, verify=False)
        response.raise_for_status()
        data = response.json()
        
        daily = data.get("daily", {})
        times = daily.get("time", [])
        temps = daily.get("temperature_2m_max", [])
        precip = daily.get("precipitation_sum", [])
        prob = daily.get("precipitation_probability_max", [])
        
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
                "max_temp_c": temps[i],
                "rain_mm": precip[i],
                "rain_prob_pct": prob[i]
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
    except Exception as e:
        logging.error(f"Failed to fetch weather data: {e}")
        return None

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
    
    llm = get_llm()
    if not llm:
        return None

    # Extract the sections
    today = weather_data.get("today", {})
    past = weather_data.get("past_days", {})
    forecast = weather_data.get("forecast", {})

    today_str = today.get("date", datetime.now().strftime("%Y-%m-%d"))
    today_day = today.get("day", datetime.now().strftime("%a"))
        
    system_prompt = "You are an expert Agronomist AI. Output ONLY raw JSON."
    user_prompt = f"""
    TODAY is {today_day}, {today_str}.
    Decide the optimal days to run the irrigation sequence for the UPCOMING 7 days (starting from today) based on weather data.
    
    RULES:
    1. Do NOT schedule irrigation on days with heavy rain (> 5mm).
    2. Try to schedule irrigation before or during hot days (> 30°C).
    3. Consider the PAST weather: if it rained heavily in the last 3 days, the soil is still moist — you can skip early days.
    4. Check "last_irrigated" — this is the date+time when the system LAST watered the plants. If it was recent (within 1 day), you may skip today.
    5. Check "irrigation_history" — this shows how many watering cycles ran on each past day (e.g. {{"2026-05-06": 3}} means 3 cycles ran on May 6th). If many cycles ran recently, the soil has plenty of water.
    6. If "irrigation_history" is empty AND the past 3 days had NO rain, prioritize watering TODAY or TOMORROW urgently.
    7. You must select between 1 and 4 days from the upcoming forecast.
    8. You must output ONLY a raw JSON object with no markdown block formatting (` ```json `), no conversational text, and exactly these keys:
    
    {{
        "selected_days": ["Mon", "Thu"],
        "reasoning": "A short 1-sentence explanation of why these days were picked."
    }}
    
    FARM DATA:
    Automation Details: {json.dumps(auto_context)}
    
    PAST 3 DAYS (actual weather that already happened):
    {json.dumps(past, indent=2)}
    
    TODAY ({today_day}, {today_str}):
    {json.dumps(today, indent=2)}
    
    UPCOMING 7-DAY FORECAST:
    {json.dumps(forecast, indent=2)}
    """
    
    result = {"decision": None, "error": None}
    
    def run_inference():
        try:
            # Run inference locally via llama-cpp-python
            response = llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,  # Low temp for deterministic logic
                max_tokens=256    # Limit output size for faster response
            )
            
            raw_text = response["choices"][0]["message"]["content"].strip()
            result["decision"] = json.loads(raw_text)
        except Exception as e:
            result["error"] = str(e)
    
    # Run inference in a thread with timeout
    thread = threading.Thread(target=run_inference)
    thread.daemon = True
    thread.start()
    thread.join(timeout=timeout)
    
    if thread.is_alive():
        logging.error(f"AI inference timed out after {timeout}s for automation {auto_context.get('automation_id')}")
        return None
    
    if result["error"]:
        logging.error(f"AI Scheduling failed for automation {auto_context.get('automation_id')}: {result['error']}")
        return None
    
    decision = result["decision"]
    if decision and ("selected_days" not in decision or "reasoning" not in decision):
        logging.error(f"AI JSON response missing required keys for automation {auto_context.get('automation_id')}")
        return None
            
    return decision

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
