# Nivixsa Dashboard: Business Roadmap 🚀

This document outlines the technical steps required to transform your private dashboard into a commercial Smart Irrigation SaaS (Software as a Service).

---

## Phase 1: Multi-Tenant Architecture (The Engine)
*Goal: Allow 100+ users to run automations at the same time.*

- [x] **Implement `UserSession` Class**: Encapsulate MQTT client, automations, and logs into a per-user object.
- [x] **Implement `SessionManager`**: A global registry to track and manage all active user sessions.
- [x] **Parallel Background Engine**: Refactor the main automation loop to iterate through every active session.
- [x] **SocketIO Rooms**: Isolate real-time updates so users only see their own device data.
- [x] **Auto-Resume**: Logic to reload and reconnect all active users from the database on server restart.

---

## Phase 2: Admin Command Center
*Goal: Give yourself total control over the platform.*

- [x] **Admin Authentication**: Professional login page with Email/Password and session persistence.
- [x] **User Management Table**:
    - [x] Search & Filter users (Dynamic table).
    - [x] Status indicators (Online/Offline vs Active/Blocked).
    - [x] **Secure Admin Impersonation**: Mandatory OTP handshake for authorized support access.
    - [x] **Deactivate Account** kill-switch (Real DB binding).
- [ ] **Global Session Invalidation**:
    - [ ] Implement database-backed session tokens to ensure logouts sync across all devices perfectly (Roadmap Item).
- [x] **System Health Monitoring**:
    - [x] Integration with `psutil` to track Server RAM/CPU (Real-time).
    - [x] Live Engine Console logs.

---

## Phase 3: Subscription & Payments (The Revenue)
*Goal: Start charging for the service.*

- [ ] **Database Migration**: Add `plan_type`, `subscription_status`, and `expiry_date` to the `users` table.
- [ ] **Paywall Logic**: 
    - [ ] Limit Free users to 1 automation.
    - [ ] Block AI features for Free users.
- [ ] **Stripe Integration**:
    - [ ] Create a Pricing Page UI.
    - [ ] Implement `stripe_checkout` redirect.
    - [ ] Create a `webhook` endpoint to automatically activate accounts after payment.

---

## Phase 4: Production Deployment
*Goal: Move from your local computer to the Cloud.*

- [ ] **Cloud Database**: Move from `cadio.db` (SQLite) to **PostgreSQL** or **MongoDB** to handle high-concurrency writes for 1,000+ users.
- [ ] **Asynchronous I/O (AsyncIO)**: Refactor the MQTT and Engine loops to use non-blocking `asyncio` for maximum performance at scale.
- [ ] **Web Server (Gunicorn)**: Use a production-grade web server instead of the Flask built-in server.
- [ ] **Nginx Reverse Proxy**: Add a layer of security and SSL (HTTPS) encryption.
- [ ] **Domain Name**: Point your professional domain (e.g., `dashboard.nivixsa.com`) to your server IP.

---

## Recommended Tech Stack for Growth:
*   **Backend**: Python (Flask-SocketIO)
*   **Database**: PostgreSQL
*   **Task Queue**: Redis / Celery (for high-volume AI scheduling)
*   **Payments**: Stripe
*   **Hosting**: AWS EC2 or DigitalOcean Droplet
