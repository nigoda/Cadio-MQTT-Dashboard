import os
import urllib.request

url = "https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf"
dest_dir = "models"
dest_file = os.path.join(dest_dir, "llama-3.2-1b-instruct.gguf")

if not os.path.exists(dest_dir):
    os.makedirs(dest_dir)

def reporthook(count, block_size, total_size):
    percent = int(count * block_size * 100 / total_size)
    if count % 1000 == 0:
        print(f"\rDownloading... {percent}%", end="")

print(f"Downloading model to {dest_file}...")
urllib.request.urlretrieve(url, dest_file, reporthook)
print("\nDownload complete!")
