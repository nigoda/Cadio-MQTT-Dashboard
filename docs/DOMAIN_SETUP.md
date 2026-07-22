# 🌐 How to Add a Custom Domain Name & Free SSL (HTTPS)

Adding a domain name (like `nivixsa.com`) is **highly recommended** for this project. Progressive Web Apps (PWAs) and Web Push Notifications **require** a secure HTTPS connection to work on mobile devices!

Here is the step-by-step guide to linking a domain name to your Google Cloud (or Oracle) server with an automatic, free SSL certificate.

---

## Step 1: Purchase a Domain Name
If you haven't already, purchase a domain name from a registrar.
* **Recommended:** [Cloudflare](https://www.cloudflare.com/products/registrar/) (cheapest, no markups) or [Namecheap](https://www.namecheap.com/).

---

## Step 2: Point your Domain to your Server (DNS)
Log in to your domain registrar's dashboard and find the **DNS settings** for your domain.
Add an **"A Record"**:
* **Type:** `A`
* **Name/Host:** `@` (or type your raw domain name, e.g., `nivixsa.com`)
* **Value/Target:** The Public IP address of your Google Cloud or Oracle Server.
* **TTL:** Automatic or 3600

*(Note: DNS changes can take a few minutes to an hour to propagate across the internet).*

---

## Step 3: Open Ports 80 and 443 on your Firewall
Your cloud server must be allowed to receive web traffic to generate the secure SSL certificate.
* **Google Cloud:** Go to your Firewall rules and ensure **Allow HTTP** (port `80`) and **Allow HTTPS** (port `443`) are enabled.
* **Oracle Cloud:** Add Ingress rules for port `80` and `443` in your VCN Security List, and run these on the server:
  ```bash
  sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
  sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
  sudo netfilter-persistent save
  ```

---

## Step 4: Add "Caddy" to your Docker setup
We will use **Caddy**, an ultra-lightweight web server that automatically fetches and renews free SSL certificates from Let's Encrypt.

1. SSH into your server and go to your project folder:
   ```bash
   cd Cadio-MQTT-Dashboard
   ```

2. Open your `docker-compose.yml` file:
   ```bash
   nano docker-compose.yml
   ```

3. Replace the entire contents with this updated version:

```yaml
version: '3.8'

services:
  nivixsa-smart-agriculture:
    build: .
    container_name: nivixsa-smart-agriculture
    restart: always
    # Note: We removed the "ports" section here because Caddy will handle it securely!
    environment:
      - TZ=UTC
    volumes:
      - ./cadio.db:/app/cadio.db
      - ./cadio.db-wal:/app/cadio.db-wal
      - ./cadio.db-shm:/app/cadio.db-shm
      - ./.encryption_key:/app/.encryption_key
      - ./.env:/app/.env

  caddy:
    image: caddy:latest
    container_name: caddy-proxy
    restart: always
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - nivixsa-smart-agriculture

volumes:
  caddy_data:
  caddy_config:
```
Save and exit (`Ctrl+X`, then `Y`, then `Enter`).

---

## Step 5: Create your Caddyfile
Now, create a configuration file that tells Caddy what your domain name is.

1. Run this command:
   ```bash
   nano Caddyfile
   ```

2. Paste this exact code, **but replace `yourdomain.com` with your actual domain!**

```text
yourdomain.com {
    reverse_proxy nivixsa-smart-agriculture:5000
}
```
Save and exit (`Ctrl+X`, then `Y`, then `Enter`).

---

## Step 6: Restart the Server!
Apply the changes by running:

```bash
DOCKER_BUILDKIT=0 docker-compose up -d --build
```

### 🎉 You are done!
Wait about 10 seconds for Caddy to talk to Let's Encrypt and generate your secure certificate. 
You can now open a browser and type: `https://yourdomain.com` and your dashboard will securely appear!
