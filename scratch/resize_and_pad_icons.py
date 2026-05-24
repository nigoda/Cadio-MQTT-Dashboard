import os
from PIL import Image

os.makedirs("static/icons", exist_ok=True)
logo_path = "static/logo_new.png"

if not os.path.exists(logo_path):
    print(f"Error: {logo_path} does not exist.")
    exit(1)

img = Image.open(logo_path)
print(f"Opened Logo.png: size={img.size}, mode={img.mode}")

# We will create transparent icons with the logo taking up to 88% of the size
for s in [72, 96, 128, 144, 192, 384, 512]:
    # 1. Create transparent background canvas of size s x s
    canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    
    # 2. Resize the logo preserving aspect ratio (max dimension = 88% of icon size)
    max_logo_size = int(s * 0.88)
    w, h = img.size
    if w > h:
        new_w = max_logo_size
        new_h = int(max_logo_size * h / w)
    else:
        new_h = max_logo_size
        new_w = int(max_logo_size * w / h)
        
    resized_logo = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    
    # 3. Paste centered
    offset_x = (s - new_w) // 2
    offset_y = (s - new_h) // 2
    
    # If logo has alpha channel, use it as a mask
    if resized_logo.mode in ("RGBA", "LA") or (resized_logo.mode == "P" and "transparency" in resized_logo.info):
        canvas.paste(resized_logo, (offset_x, offset_y), resized_logo)
    else:
        canvas.paste(resized_logo, (offset_x, offset_y))
        
    # Save as PNG
    canvas.save(f"static/icons/icon-{s}x{s}.png", "PNG")
    print(f"  Generated padded maskable icon: icon-{s}x{s}.png ({new_w}x{new_h})")

print("All padded icons successfully generated with correct aspect ratio.")
