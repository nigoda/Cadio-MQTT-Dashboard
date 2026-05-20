"""Check in-memory runtime data by connecting to the running app via a quick HTTP/socket check."""
# Instead, let's look at date format matching issue
from datetime import datetime, timedelta

# This is what the JS chart generates for dates:
dates_js = []
for i in range(6, -1, -1):
    d = datetime.now() - timedelta(days=i)
    dates_js.append(d.strftime("%Y-%m-%d"))

print("JS chart dates (last 7 days):")
for d in dates_js:
    print(f"  {d}")

# This is what the engine stores as keys:
print(f"\nEngine format example: {datetime.now().strftime('%Y-%m-%d')}")

# Check with UTC vs local time
from datetime import timezone
utc_now = datetime.now(timezone.utc)
local_now = datetime.now()
print(f"\nUTC date:   {utc_now.strftime('%Y-%m-%d')}")
print(f"Local date: {local_now.strftime('%Y-%m-%d')}")
