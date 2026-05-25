import os
from PIL import Image

os.makedirs("static/icons", exist_ok=True)
logo_path = "static/logo_new.png"

if not os.path.exists(logo_path):
    print(f"Error: {logo_path} does not exist.")
    exit(1)

img = Image.open(logo_path)
print(f"Opened Logo.png: size={img.size}, mode={img.mode}")

def create_gradient_canvas(s):
    # Generates a premium dark titanium-gray to rich charcoal vertical gradient
    # Top gray: #3c4046 (60, 64, 70), Bottom charcoal: #1e2022 (30, 32, 34)
    canvas = Image.new("RGBA", (s, s))
    start_color = (60, 64, 70, 255)
    end_color = (30, 32, 34, 255)
    
    for y in range(s):
        ratio = y / (s - 1) if s > 1 else 0
        r = int(start_color[0] * (1 - ratio) + end_color[0] * ratio)
        g = int(start_color[1] * (1 - ratio) + end_color[1] * ratio)
        b = int(start_color[2] * (1 - ratio) + end_color[2] * ratio)
        a = int(start_color[3] * (1 - ratio) + end_color[3] * ratio)
        
        for x in range(s):
            canvas.putpixel((x, y), (r, g, b, a))
    return canvas

# We will create premium gray gradient maskable icons with the logo taking up to 88% of the size
for s in [72, 96, 128, 144, 192, 384, 512]:
    # 1. Create linear vertical gradient background canvas of size s x s
    canvas = create_gradient_canvas(s)
    
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
