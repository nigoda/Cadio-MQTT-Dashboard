import os
import urllib.request

# Phi-4-mini-instruct Q4_K_M (~2.5GB)
url = "https://huggingface.co/microsoft/Phi-4-mini-instruct-gguf/resolve/main/Phi-4-mini-instruct-Q4_K_M.gguf"
dest_dir = "models"
dest_file = os.path.join(dest_dir, "Phi-4-mini-instruct-Q4_K_M.gguf")

if not os.path.exists(dest_dir):
    os.makedirs(dest_dir)

if os.path.exists(dest_file):
    size_mb = os.path.getsize(dest_file) / (1024 * 1024)
    print(f"Model already exists at {dest_file} ({size_mb:.1f} MB)")
    print("Delete it manually if you want to re-download.")
else:
    def reporthook(count, block_size, total_size):
        percent = int(count * block_size * 100 / total_size)
        mb_done = count * block_size / (1024 * 1024)
        mb_total = total_size / (1024 * 1024)
        if count % 500 == 0:
            print(f"\rDownloading... {percent}% ({mb_done:.0f}/{mb_total:.0f} MB)", end="")

    print(f"Downloading Phi-4-mini-instruct Q4_K_M to {dest_file}...")
    print("This is ~2.5 GB, it may take a few minutes.\n")
    urllib.request.urlretrieve(url, dest_file, reporthook)
    print("\n\nDownload complete!")
