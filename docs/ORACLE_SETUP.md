# ☁️ Hosting on Oracle Cloud (Always Free)

Oracle Cloud provides the most generous "Always Free" tier available, granting you an ARM-based Virtual Private Server (VPS) that runs 24/7 with a persistent hard drive. This is the **perfect** environment for your Cadio-MQTT-Dashboard because it ensures your SQLite database (`cadio.db`) is never deleted and your automation engine never sleeps.

I have just created a `Dockerfile` and `docker-compose.yml` in your repository to make deployment incredibly easy.

Follow these steps to get your dashboard live on the internet!

---

## Step 1: Create Your Oracle VM

1. Go to [Oracle Cloud](https://cloud.oracle.com/) and sign up for a Free Tier account.
2. In the Oracle Cloud Console, go to **Compute** -> **Instances** -> **Create Instance**.
3. **Name:** `nivixsa-smart-agriculture`
4. **Image and Shape:**
   * **Image:** Change to **Ubuntu 22.04** (or 24.04).
   * **Shape:** Choose **Ampere (ARM)**. You can slide the slider up to **2-4 OCPUs** and **12-24 GB RAM** (this is all completely free in the Always Free tier).
5. **Networking:** Leave default, but ensure "Assign a public IPv4 address" is checked.
6. **Add SSH Keys:** Select **Generate a key pair for me** and **Download the private key**. You will need this file to connect to your server!
7. Click **Create**. Wait 1-2 minutes for the instance to say "Running" and note down its **Public IP Address**.

---

## Step 2: Open Firewall Ports (Oracle Dashboard)

By default, Oracle blocks all incoming web traffic. We need to open port `5000` (or `80`/`443` if you use a reverse proxy later).

1. Click on the name of your Subnet (e.g., `Public Subnet-....`) on the Instance details page.
2. Click on the **Security List** (e.g., `Default Security List for...`).
3. Click **Add Ingress Rules**:
   * **Source CIDR:** `0.0.0.0/0`
   * **IP Protocol:** TCP
   * **Destination Port Range:** `5000`
4. Click **Add Ingress Rules**.

---

## Step 3: Connect to Your Server

Open a terminal on your laptop (Command Prompt, PowerShell, or Mac Terminal) and connect using the Private Key you downloaded:

```bash
# On Mac/Linux, you must fix permissions first: chmod 400 path/to/your/private-key.key
ssh -i "path/to/your/private-key.key" ubuntu@<YOUR_PUBLIC_IP>
```

---

## Step 4: Open Firewall Ports (Ubuntu Server)

Even though you opened the port on the Oracle website, Ubuntu's internal firewall (`iptables`) also blocks it. Run these commands on your server:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 5000 -j ACCEPT
sudo netfilter-persistent save
```

---

## Step 5: Install Docker & Clone Your Repo

Run these commands to install Docker, download your code, and prepare the files:

```bash
# 1. Install Docker & Docker Compose
sudo apt update
sudo apt install -y docker.io docker-compose git

# 2. Add your user to the docker group (so you don't have to type 'sudo docker')
sudo usermod -aG docker $USER
newgrp docker

# 3. Clone your GitHub repository (replace with your actual URL)
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

## Step 6: Start the Dashboard!

With everything prepared, launch your application in the background:

```bash
DOCKER_BUILDKIT=0 docker-compose up -d --build
```

Docker will now download Python, install all dependencies, and spin up your dashboard. 

> [!TIP]
> You can now access your live dashboard from any browser! Just navigate to:
> **`http://<YOUR_PUBLIC_IP>:5000`**

### Useful Commands

* **To see live logs (useful for debugging automations or AI):**
  `docker logs -f nivixsa-smart-agriculture`
* **To restart the server (e.g., if you edit `.env`):**
  `docker-compose restart`
* **To update your code in the future:**
  ```bash
  git pull
  docker-compose down
  DOCKER_BUILDKIT=0 docker-compose up -d --build
  ```
