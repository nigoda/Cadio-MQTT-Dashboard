import os
from PIL import Image

os.makedirs("static/icons", exist_ok=True)
img = Image.open("static/Logo.png")

for s in [72, 96, 128, 144, 192, 384, 512]:
    resized = img.resize((s, s), Image.LANCZOS)
    resized.save(f"static/icons/icon-{s}x{s}.png")
    print(f"  Created icon-{s}x{s}.png")

print("All PWA icons generated.")
