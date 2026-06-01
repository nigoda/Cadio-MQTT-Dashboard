# ☁️ Hosting on Google Cloud Platform (Always Free)

Google Cloud Platform (GCP) offers an "Always Free" tier that includes a permanent `e2-micro` virtual machine and 30 GB of persistent standard disk storage. This is excellent for your dashboard as it ensures 24/7 uptime and keeps your SQLite databases safe.

The `Dockerfile` and `docker-compose.yml` already in your repository work identically on Google Cloud! Just follow these steps to get your VM running.

---

## Step 1: Create Your Google Cloud VM

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a new project.
2. Ensure billing is enabled (you must add a credit card to prove you aren't a bot, but as long as you stay within the free limits, you won't be charged).
3. Navigate to **Compute Engine** -> **VM instances** -> **Create Instance**.
4. **Name:** `cadio-dashboard`
5. **Region and Zone:** **CRITICAL:** To qualify for the Always Free tier, you MUST choose one of these three US regions:
   * `us-west1` (Oregon)
   * `us-central1` (Iowa)
   * `us-east1` (South Carolina)
6. **Machine Configuration:** 
   * Series: **E2**
   * Machine type: **e2-micro** (2 vCPU, 1 GB memory)
7. **Boot Disk:** 
   * Change the OS to **Ubuntu** (Version: Ubuntu 22.04 LTS or 24.04 LTS).
   * **Boot disk type:** Standard persistent disk.
   * **Size:** Up to 30 GB (Free tier limit).
8. **Firewall:** Check both **Allow HTTP traffic** and **Allow HTTPS traffic**.
9. Click **Create**. Once it's running, note down the **External IP**.

---

## Step 2: Open Firewall Ports (VPC Network)

By default, Google opens port 80 and 443 if you checked the boxes above, but your app runs on port `5000`. We need to open it.

1. In the Google Cloud Console, search for **Firewall** (under VPC network).
2. Click **Create Firewall Rule**.
3. **Name:** `allow-port-5000`
4. **Targets:** `All instances in the network`
5. **Source IPv4 ranges:** `0.0.0.0/0`
6. **Specified protocols and ports:** Check `TCP` and type `5000`.
7. Click **Create**.

*(Note: Unlike Oracle, Ubuntu on GCP doesn't usually run `iptables` rules that block custom ports, so this cloud firewall rule is all you need!)*

---

## Step 3: Connect to Your Server

The easiest way to connect is directly through your browser.
1. Go back to **Compute Engine** -> **VM instances**.
2. Click the **SSH** button next to your `cadio-dashboard` instance. This will open a terminal window in your browser.

---

## Step 4: Install Docker & Clone Your Repo

Inside the SSH terminal, run these commands to install Docker and download your code:

```bash
# 1. Install Docker & Docker Compose
sudo apt-get update
sudo apt-get install -y docker.io docker-compose git

# 2. Add your user to the docker group
sudo usermod -aG docker $USER
newgrp docker

# 3. Clone your GitHub repository (replace with your actual URL if different)
git clone https://github.com/nigoda/Cadio-MQTT-Dashboard.git
cd Cadio-MQTT-Dashboard

# Checkout your production branch if needed
git checkout Server-Production-code

# 4. Create blank database files so Docker doesn't map them as directories
touch cadio.db cadio.db-wal cadio.db-shm .encryption_key .env

# 5. Add your secure .env keys!
nano .env
# (Paste your GEMINI_API_KEY, VAPID_PUBLIC_KEY, etc. in here, then press Ctrl+X, Y, Enter)
```

---

## Step 5: Start the Dashboard!

With everything prepared, launch your application in the background:

```bash
docker-compose up -d --build
```

Docker will now download Python, install all dependencies, and spin up your dashboard. 

> [!TIP]
> You can now access your live dashboard from any browser! Just navigate to:
> **`http://<YOUR_EXTERNAL_IP>:5000`**

### Useful Commands

* **To see live logs:** `docker-compose logs -f`
* **To update your code in the future:**
  ```bash
  git pull
  docker-compose up -d --build
  ```
