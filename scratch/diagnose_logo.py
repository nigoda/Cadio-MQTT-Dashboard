from PIL import Image

def diagnose(img_path):
    img = Image.open(img_path)
    print(f"Diagnostics for {img_path}:")
    print(f"  Size: {img.size}")
    print(f"  Mode: {img.mode}")
    
    # Check some corner pixel colors
    w, h = img.size
    corners = [
        ("Top-Left (0,0)", (0, 0)),
        ("Top-Right (w-1,0)", (w - 1, 0)),
        ("Bottom-Left (0,h-1)", (0, h - 1)),
        ("Bottom-Right (w-1,h-1)", (w - 1, h - 1)),
        ("Center (w/2, h/2)", (w // 2, h // 2))
    ]
    for name, pos in corners:
        color = img.getpixel(pos)
        print(f"  {name} color: {color}")

diagnose("static/Logo.png")
