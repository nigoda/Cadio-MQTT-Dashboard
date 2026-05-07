import os
from google import genai
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

print("Listing models...")
try:
    for m in client.models.list():
        print(f" - {m.name} ({m.display_name})")
except Exception as e:
    print(f"Error: {e}")
