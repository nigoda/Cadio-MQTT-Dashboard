"""Test script to run the exact AI prompt against the local model."""
import json
import os
import sys

MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "llama-3.2-1b-instruct.gguf")

if not os.path.exists(MODEL_PATH):
    print(f"ERROR: Model not found at {MODEL_PATH}")
    sys.exit(1)

print("Loading model... (this may take a few seconds)")
from llama_cpp import Llama
llm = Llama(model_path=MODEL_PATH, n_ctx=2048, verbose=False)
print("Model loaded!\n")

system_prompt = "You are an expert Agronomist AI. Output ONLY raw JSON."

user_prompt = """TODAY is Wed, 2026-05-07.
Decide the optimal days to run the irrigation sequence for the UPCOMING 
7 days (starting from today) based on weather data.

RULES:
1. Do NOT schedule irrigation on days with heavy rain (> 5mm).
2. Try to schedule irrigation before or during hot days (> 30°C).
3. Consider the PAST weather: if it rained heavily in the last 3 days, 
   the soil is still moist — you can skip early days.
4. Check "last_irrigated" — this is the date+time when the system LAST 
   watered the plants. If it was recent (within 1 day), you may skip today.
5. Check "irrigation_history" — this shows how many watering cycles ran 
   on each past day (e.g. {"2026-05-06": 3} means 3 cycles ran on May 6th). 
   If many cycles ran recently, the soil has plenty of water.
6. If "irrigation_history" is empty AND the past 3 days had NO rain, 
   prioritize watering TODAY or TOMORROW urgently.
7. You must select between 1 and 4 days from the upcoming forecast.
8. You must output ONLY a raw JSON object with no markdown block formatting, 
   no conversational text, and exactly these keys:

{
    "selected_days": ["Mon", "Thu"],
    "reasoning": "A short 1-sentence explanation of why these days were picked."
}

FARM DATA:
Automation Details: {
    "automation_id": "auto_9j2k",
    "name": "Vegetable Patch",
    "description": "Sensitive tomatoes, need soil moisture high.",
    "time_ranges": "05:00 to 07:00, 18:00 to 19:00",
    "total_water_duration_seconds": 3600,
    "is_24hr_active": false,
    "last_irrigated": "2026-05-06 06:15",
    "cycles_completed_today": 0,
    "max_cycles_per_day": 3,
    "irrigation_history": {
        "2026-05-04": 3,
        "2026-05-05": 2,
        "2026-05-06": 1
    },
    "lat": 12.8406,
    "lon": 77.6772
}

PAST 3 DAYS (actual weather that already happened):
{
    "Sun (2026-05-04)": {"date": "2026-05-04", "max_temp_c": 33.1, "rain_mm": 0.0, "rain_prob_pct": 5},
    "Mon (2026-05-05)": {"date": "2026-05-05", "max_temp_c": 31.8, "rain_mm": 0.0, "rain_prob_pct": 10},
    "Tue (2026-05-06)": {"date": "2026-05-06", "max_temp_c": 24.2, "rain_mm": 12.4, "rain_prob_pct": 95}
}

TODAY (Wed, 2026-05-07):
{
    "date": "2026-05-07", "day": "Wed", "max_temp_c": 28.5, "rain_mm": 0.0, "rain_prob_pct": 15
}

UPCOMING 7-DAY FORECAST:
{
    "Thu (2026-05-08)": {"date": "2026-05-08", "max_temp_c": 29.3, "rain_mm": 0.0, "rain_prob_pct": 5},
    "Fri (2026-05-09)": {"date": "2026-05-09", "max_temp_c": 32.5, "rain_mm": 0.0, "rain_prob_pct": 0},
    "Sat (2026-05-10)": {"date": "2026-05-10", "max_temp_c": 33.0, "rain_mm": 0.0, "rain_prob_pct": 0},
    "Sun (2026-05-11)": {"date": "2026-05-11", "max_temp_c": 30.1, "rain_mm": 2.1, "rain_prob_pct": 40},
    "Mon (2026-05-12)": {"date": "2026-05-12", "max_temp_c": 26.8, "rain_mm": 8.5, "rain_prob_pct": 85},
    "Tue (2026-05-13)": {"date": "2026-05-13", "max_temp_c": 27.4, "rain_mm": 0.3, "rain_prob_pct": 20},
    "Wed (2026-05-14)": {"date": "2026-05-14", "max_temp_c": 31.2, "rain_mm": 0.0, "rain_prob_pct": 5}
}"""

print("=" * 60)
print("SENDING PROMPT TO AI MODEL...")
print("=" * 60)

response = llm.create_chat_completion(
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ],
    response_format={"type": "json_object"},
    temperature=0.1,
    max_tokens=256
)

raw_text = response["choices"][0]["message"]["content"].strip()

print("\n" + "=" * 60)
print("RAW AI RESPONSE:")
print("=" * 60)
print(raw_text)

try:
    parsed = json.loads(raw_text)
    print("\n" + "=" * 60)
    print("PARSED JSON:")
    print("=" * 60)
    print(json.dumps(parsed, indent=2))
    
    if "selected_days" in parsed:
        print(f"\n✅ Selected Days: {parsed['selected_days']}")
    if "reasoning" in parsed:
        print(f"💬 Reasoning: {parsed['reasoning']}")
except json.JSONDecodeError as e:
    print(f"\n❌ Failed to parse JSON: {e}")
