import os
from PIL import Image
from collections import deque

def make_logo_transparent(img_path):
    if not os.path.exists(img_path):
        print(f"Error: {img_path} does not exist.")
        return
        
    img = Image.open(img_path).convert("RGBA")
    width, height = img.size
    pixels = img.load()
    
    # Track visited pixels in flood fill
    visited = [[False for _ in range(height)] for _ in range(width)]
    background_mask = [[False for _ in range(height)] for _ in range(width)]
    
    # A pixel is considered "dark background" if R, G, B are all < 60
    # (Since our corners were around 24-49 range)
    def is_bg_color(x, y):
        r, g, b, a = pixels[x, y]
        # Ignore already transparent pixels
        if a == 0:
            return True
        return r < 60 and g < 60 and b < 60
        
    # Queue for BFS
    queue = deque()
    
    # Add all border pixels to queue if they are background color
    for x in range(width):
        if is_bg_color(x, 0):
            queue.append((x, 0))
            visited[x][0] = True
        if is_bg_color(x, height - 1):
            queue.append((x, height - 1))
            visited[x][height - 1] = True
            
    for y in range(height):
        if is_bg_color(0, y):
            queue.append((0, y))
            visited[0][y] = True
        if is_bg_color(width - 1, y):
            queue.append((width - 1, y))
            visited[width - 1][y] = True
            
    # Perform BFS flood fill
    bg_pixel_count = 0
    while queue:
        cx, cy = queue.popleft()
        background_mask[cx][cy] = True
        bg_pixel_count += 1
        
        # Check 4-way neighbors
        for nx, ny in [(cx+1, cy), (cx-1, cy), (cx, cy+1), (cx, cy-1)]:
            if 0 <= nx < width and 0 <= ny < height:
                if not visited[nx][ny] and is_bg_color(nx, ny):
                    visited[nx][ny] = True
                    queue.append((nx, ny))
                    
    # Convert all identified background pixels to transparent
    if bg_pixel_count > 0:
        for x in range(width):
            for y in range(height):
                if background_mask[x][y]:
                    pixels[x, y] = (0, 0, 0, 0)
                    
        img.save(img_path, "PNG")
        print(f"Successfully made the dark background transparent! Cleaned up {bg_pixel_count} connected dark pixels.")
    else:
        print("No dark border background pixels found.")

if __name__ == "__main__":
    make_logo_transparent("static/logo_new.png")
