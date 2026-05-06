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

def get_7_day_forecast(lat=DEFAULT_LAT, lon=DEFAULT_LON):
    """Fetches a 7-day forecast from Open-Meteo (No API Key required)."""
    url = f"https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": ["temperature_2m_max", "precipitation_sum", "precipitation_probability_max"],
        "timezone": "auto"
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
        
        forecast = {}
        for i in range(len(times)):
            date_str = times[i]
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            day_name = dt.strftime("%a")
            
            if day_name not in forecast:
                forecast[day_name] = {
                    "date": date_str,
                    "max_temp_c": temps[i],
                    "rain_mm": precip[i],
                    "rain_prob_pct": prob[i]
                }
                
        return forecast
    except Exception as e:
        logging.error(f"Failed to fetch weather data: {e}")
        return None

def build_automation_context(auto_id, auto_data):
    """Extracts relevant info from an automation config for the AI."""
    total_duration = 0
    for action in auto_data.get("actions", []):
        try:
            total_duration += int(action.get("duration", 0))
        except ValueError:
            pass

    return {
        "automation_id": auto_id,
        "name": auto_data.get("name", "Unknown"),
        "description": auto_data.get("description", ""),
        "time_range": f'{auto_data.get("startTime", "00:00")} to {auto_data.get("endTime", "23:59")}',
        "total_water_duration_seconds": total_duration,
        "is_24hr_active": auto_data.get("is24hr", False),
        "lat": auto_data.get("lat"),
        "lon": auto_data.get("lon")
    }

def get_ai_schedule_decision(forecast_data, auto_context, timeout=60):
    """Executes the local AI model to get a scheduling decision."""
    import threading
    
    llm = get_llm()
    if not llm:
        return None
        
    system_prompt = "You are an expert Agronomist AI. Output ONLY raw JSON."
    user_prompt = f"""
    Decide the optimal days to run the irrigation sequence in the upcoming 7 days based on the weather forecast.
    
    RULES:
    1. Do NOT schedule irrigation on days with heavy rain (> 5mm).
    2. Try to schedule irrigation before or during hot days (> 30°C).
    3. You must select between 1 and 4 days.
    4. You must output ONLY a raw JSON object with no markdown block formatting (` ```json `), no conversational text, and exactly these keys:
    
    {{
        "selected_days": ["Mon", "Thu"],
        "reasoning": "A short 1-sentence explanation of why these days were picked."
    }}
    
    FARM DATA:
    Automation Details: {json.dumps(auto_context)}
    7-Day Forecast: {json.dumps(forecast_data)}
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
            "startTime": "05:00",
            "endTime": "07:00",
            "is24hr": False,
            "actions": [{"duration": 1200}]
        }
        ctx = build_automation_context("test_auto", dummy_auto)
        decision = get_ai_schedule_decision(forecast, ctx)
        if decision:
            print("\nAI Decision:")
            print(json.dumps(decision, indent=2))
