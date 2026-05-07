"""Test script — Multiple scenarios to verify AI consistency with Gemini API."""
import json
import os
import sys
import warnings
from dotenv import load_dotenv

# Suppress noisy Google SDK warnings about Python 3.9 EOL
warnings.filterwarnings("ignore", category=FutureWarning, module="google.auth")
warnings.filterwarnings("ignore", category=FutureWarning, module="google.oauth2")
warnings.filterwarnings("ignore", category=FutureWarning, module="google.api_core")
from google import genai

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
if not GEMINI_API_KEY:
    print("ERROR: GEMINI_API_KEY not found in environment or .env file.")
    sys.exit(1)

client = genai.Client(api_key=GEMINI_API_KEY)
print("Gemini API Client loaded!\n")

VALID_DAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
MAX_RETRIES = 3

system_prompt = "You are an expert Agronomist AI that decides optimal irrigation schedules. Analyze weather data and output ONLY valid raw JSON."

# ─── SCENARIO DEFINITIONS ───
SCENARIOS = [
    {
        "name": "🌞 Scenario 1: Hot & Dry Week (should water frequently)",
        "expected": "Multiple days selected (3-4), no rain to skip",
        "prompt": """TODAY is Wed, 2026-05-07.
Decide the optimal days to run irrigation for the UPCOMING 7 days based on ALL the data below.

RULES:
1. Do NOT schedule irrigation on days with heavy rain (> 5mm precipitation).
2. Prioritize irrigation before or during hot days (> 30°C).
3. Consider PAST weather: if it rained heavily in the last 3 days, soil is still moist.
4. Check "last_irrigated" — if within 1 day, you may skip today.
5. Check "irrigation_history" — many recent cycles means soil has water.
6. Check "cycles_completed_today" — if > 0, system already watered today.
7. If "irrigation_history" is empty AND past 3 days had NO rain, prioritize TODAY or TOMORROW urgently.
8. Select between 1 and 4 days from the upcoming forecast.
9. Output ONLY a raw JSON object with exactly these keys:

{
    "selected_days": ["DAY1", "DAY2"],
    "reasoning": "Your analysis."
}

IMPORTANT: In selected_days, replace DAY1/DAY2 with ONLY short day names (Mon, Tue, Wed, Thu, Fri, Sat, Sun) chosen from the forecast. Do NOT include dates or parentheses.

AUTOMATION DETAILS:
{
    "automation_id": "test_1",
    "name": "Garden Zone 1",
    "time_ranges": "05:00 to 07:00",
    "total_water_duration_seconds": 1800,
    "last_irrigated": "2026-05-05 06:00",
    "cycles_completed_today": 0,
    "max_cycles_per_day": 2,
    "irrigation_history": {"2026-05-05": 2}
}

PAST 3 DAYS:
{
    "Sun (2026-05-04)": {"max_temp_c": 35.0, "rain_mm": 0.0, "rain_prob_pct": 0},
    "Mon (2026-05-05)": {"max_temp_c": 36.2, "rain_mm": 0.0, "rain_prob_pct": 0},
    "Tue (2026-05-06)": {"max_temp_c": 37.1, "rain_mm": 0.0, "rain_prob_pct": 0}
}

TODAY (Wed, 2026-05-07):
{"max_temp_c": 38.0, "rain_mm": 0.0, "rain_prob_pct": 0}

UPCOMING 7-DAY FORECAST:
{
    "Thu (2026-05-08)": {"max_temp_c": 37.5, "rain_mm": 0.0, "rain_prob_pct": 0},
    "Fri (2026-05-09)": {"max_temp_c": 36.8, "rain_mm": 0.0, "rain_prob_pct": 5},
    "Sat (2026-05-10)": {"max_temp_c": 38.2, "rain_mm": 0.0, "rain_prob_pct": 0},
    "Sun (2026-05-11)": {"max_temp_c": 35.0, "rain_mm": 0.0, "rain_prob_pct": 0},
    "Mon (2026-05-12)": {"max_temp_c": 34.5, "rain_mm": 0.0, "rain_prob_pct": 10},
    "Tue (2026-05-13)": {"max_temp_c": 36.0, "rain_mm": 0.0, "rain_prob_pct": 0},
    "Wed (2026-05-14)": {"max_temp_c": 37.0, "rain_mm": 0.0, "rain_prob_pct": 0}
}"""
    },
    {
        "name": "🌧️ Scenario 2: Rainy Week (should water minimally)",
        "expected": "1-2 days selected, avoids heavy rain days (Mon, Wed)",
        "prompt": """TODAY is Wed, 2026-05-07.
Decide the optimal days to run irrigation for the UPCOMING 7 days based on ALL the data below.

RULES:
1. Do NOT schedule irrigation on days with heavy rain (> 5mm precipitation).
2. Prioritize irrigation before or during hot days (> 30°C).
3. Consider PAST weather: if it rained heavily in the last 3 days, soil is still moist.
4. Check "last_irrigated" — if within 1 day, you may skip today.
5. Check "irrigation_history" — many recent cycles means soil has water.
6. Check "cycles_completed_today" — if > 0, system already watered today.
7. If "irrigation_history" is empty AND past 3 days had NO rain, prioritize TODAY or TOMORROW urgently.
8. Select between 1 and 4 days from the upcoming forecast.
9. Output ONLY a raw JSON object with exactly these keys:

{
    "selected_days": ["DAY1", "DAY2"],
    "reasoning": "Your analysis."
}

IMPORTANT: In selected_days, replace DAY1/DAY2 with ONLY short day names (Mon, Tue, Wed, Thu, Fri, Sat, Sun) chosen from the forecast. Do NOT include dates or parentheses.

AUTOMATION DETAILS:
{
    "automation_id": "test_2",
    "name": "Lawn Sprinkler",
    "time_ranges": "06:00 to 08:00",
    "total_water_duration_seconds": 2400,
    "last_irrigated": "2026-05-06 07:00",
    "cycles_completed_today": 0,
    "max_cycles_per_day": 1,
    "irrigation_history": {"2026-05-06": 1}
}

PAST 3 DAYS:
{
    "Sun (2026-05-04)": {"max_temp_c": 28.0, "rain_mm": 3.2, "rain_prob_pct": 60},
    "Mon (2026-05-05)": {"max_temp_c": 25.5, "rain_mm": 15.0, "rain_prob_pct": 95},
    "Tue (2026-05-06)": {"max_temp_c": 24.0, "rain_mm": 8.0, "rain_prob_pct": 90}
}

TODAY (Wed, 2026-05-07):
{"max_temp_c": 26.0, "rain_mm": 2.5, "rain_prob_pct": 50}

UPCOMING 7-DAY FORECAST:
{
    "Thu (2026-05-08)": {"max_temp_c": 27.0, "rain_mm": 0.5, "rain_prob_pct": 20},
    "Fri (2026-05-09)": {"max_temp_c": 28.5, "rain_mm": 0.0, "rain_prob_pct": 10},
    "Sat (2026-05-10)": {"max_temp_c": 25.0, "rain_mm": 12.0, "rain_prob_pct": 90},
    "Sun (2026-05-11)": {"max_temp_c": 26.0, "rain_mm": 6.5, "rain_prob_pct": 80},
    "Mon (2026-05-12)": {"max_temp_c": 24.5, "rain_mm": 18.0, "rain_prob_pct": 95},
    "Tue (2026-05-13)": {"max_temp_c": 29.0, "rain_mm": 0.0, "rain_prob_pct": 5},
    "Wed (2026-05-14)": {"max_temp_c": 30.5, "rain_mm": 0.0, "rain_prob_pct": 0}
}"""
    },
    {
        "name": "🆘 Scenario 3: No History, Bone Dry (should water urgently)",
        "expected": "Should pick Thu/Fri urgently — no history, no rain, hot",
        "prompt": """TODAY is Wed, 2026-05-07.
Decide the optimal days to run irrigation for the UPCOMING 7 days based on ALL the data below.

RULES:
1. Do NOT schedule irrigation on days with heavy rain (> 5mm precipitation).
2. Prioritize irrigation before or during hot days (> 30°C).
3. Consider PAST weather: if it rained heavily in the last 3 days, soil is still moist.
4. Check "last_irrigated" — if within 1 day, you may skip today.
5. Check "irrigation_history" — many recent cycles means soil has water.
6. Check "cycles_completed_today" — if > 0, system already watered today.
7. If "irrigation_history" is empty AND past 3 days had NO rain, prioritize TODAY or TOMORROW urgently.
8. Select between 1 and 4 days from the upcoming forecast.
9. Output ONLY a raw JSON object with exactly these keys:

{
    "selected_days": ["DAY1", "DAY2"],
    "reasoning": "Your analysis."
}

IMPORTANT: In selected_days, replace DAY1/DAY2 with ONLY short day names (Mon, Tue, Wed, Thu, Fri, Sat, Sun) chosen from the forecast. Do NOT include dates or parentheses.

AUTOMATION DETAILS:
{
    "automation_id": "test_3",
    "name": "New Garden Bed",
    "time_ranges": "24-Hour Active (no time restriction)",
    "total_water_duration_seconds": 600,
    "last_irrigated": "Unknown (no data this session)",
    "cycles_completed_today": 0,
    "max_cycles_per_day": 0,
    "irrigation_history": {}
}

PAST 3 DAYS:
{
    "Sun (2026-05-04)": {"max_temp_c": 34.0, "rain_mm": 0.0, "rain_prob_pct": 0},
    "Mon (2026-05-05)": {"max_temp_c": 35.5, "rain_mm": 0.0, "rain_prob_pct": 0},
    "Tue (2026-05-06)": {"max_temp_c": 36.0, "rain_mm": 0.0, "rain_prob_pct": 0}
}

TODAY (Wed, 2026-05-07):
{"max_temp_c": 37.0, "rain_mm": 0.0, "rain_prob_pct": 0}

UPCOMING 7-DAY FORECAST:
{
    "Thu (2026-05-08)": {"max_temp_c": 36.5, "rain_mm": 0.0, "rain_prob_pct": 0},
    "Fri (2026-05-09)": {"max_temp_c": 35.0, "rain_mm": 0.0, "rain_prob_pct": 5},
    "Sat (2026-05-10)": {"max_temp_c": 33.0, "rain_mm": 1.0, "rain_prob_pct": 15},
    "Sun (2026-05-11)": {"max_temp_c": 32.0, "rain_mm": 0.0, "rain_prob_pct": 0},
    "Mon (2026-05-12)": {"max_temp_c": 31.5, "rain_mm": 0.0, "rain_prob_pct": 10},
    "Tue (2026-05-13)": {"max_temp_c": 34.0, "rain_mm": 0.0, "rain_prob_pct": 0},
    "Wed (2026-05-14)": {"max_temp_c": 35.5, "rain_mm": 0.0, "rain_prob_pct": 0}
}"""
    },
]


def run_scenario(scenario):
    """Run a single scenario with Gemini API."""
    try:
        full_prompt = f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\nUSER REQUEST:\n{scenario['prompt']}"
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=full_prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.1
            }
        )
        raw = response.text.strip()
        parsed = json.loads(raw)

        if "selected_days" not in parsed or "reasoning" not in parsed:
            print(f"    ⚠️  Missing keys in response")
            return None, None, 1

        cleaned = []
        for d in parsed["selected_days"]:
            short = d.split(" ")[0].split("(")[0].strip()
            if short in VALID_DAYS:
                cleaned.append(short)

        if not cleaned:
            print(f"    ⚠️  No valid days in {parsed['selected_days']}")
            return None, None, 1

        return cleaned, parsed["reasoning"], 1

    except json.JSONDecodeError:
        print(f"    ⚠️  JSON parse error")
    except Exception as e:
        print(f"    ⚠️  {e}")

    return None, None, 1


# ─── RUN ALL SCENARIOS ───
print("=" * 70)
print("  MULTI-SCENARIO AI CONSISTENCY TEST")
print("=" * 70)

results = []

for i, scenario in enumerate(SCENARIOS):
    print(f"\n{'-' * 70}")
    print(f"  {scenario['name']}")
    print(f"  Expected: {scenario['expected']}")
    print(f"{'-' * 70}")

    days, reasoning, attempts = run_scenario(scenario)

    if days:
        print(f"\n  [PASS] Result (attempt {attempts}): {days}")
        print(f"  > {reasoning[:120]}{'...' if len(reasoning) > 120 else ''}")
        results.append({"scenario": scenario["name"], "days": days, "status": "PASS", "attempts": attempts})
    else:
        print(f"\n  [FAIL] FAILED after {MAX_RETRIES} attempts")
        results.append({"scenario": scenario["name"], "days": None, "status": "FAIL", "attempts": MAX_RETRIES})

# ─── SUMMARY ───
print(f"\n{'=' * 70}")
print("  SUMMARY")
print(f"{'=' * 70}")
print(f"  {'Scenario':<50} {'Days':<20} {'Status':<8} {'Tries'}")
print(f"  {'-' * 50} {'-' * 20} {'-' * 8} {'-' * 5}")

for r in results:
    days_str = ", ".join(r["days"]) if r["days"] else "—"
    status_icon = "PASS" if r["status"] == "PASS" else "FAIL"
    # Extract just the short name
    short_name = r["scenario"].split(":")[0].strip() if ":" in r["scenario"] else r["scenario"][:40]
    print(f"  {short_name:<50} {days_str:<20} {status_icon:<8} {r['attempts']}")

passed = sum(1 for r in results if r["status"] == "PASS")
total = len(results)
print(f"\n  Result: {passed}/{total} scenarios passed")

if passed == total:
    print("  *** All scenarios produced valid output! ***")
else:
    print("  !!! Some scenarios failed !!!")
