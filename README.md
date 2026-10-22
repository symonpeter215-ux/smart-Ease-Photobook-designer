# Ease Subscription Manager

A dedicated, administrative subscription management and monitoring application for the owner of **Smart Ease Photobook Designer** & **Ease Select**.

> [!NOTE]
> This application is strictly the **Subscription Management Console** for the product owner/administrator (monitoring registered users, active subscriptions, online/offline presence, license leases, packages, and recorded revenue). It is **NOT** the customer-facing payment website (which is maintained separately in `ease creative suite payments`).

---

## 🌟 Key Features

- **Executive KPI Dashboard**: Real-time calculation of Active Users, Subscriptions, Expiring Soon, Expired, Today's Gross Earnings, Monthly Revenue, and Lifetime Pro Users.
- **Dynamic Revenue Analytics**: Interactive monthly revenue chart with day-by-day earnings breakdown and verified transaction ledger.
- **Client License Verification API**: REST endpoints for desktop client applications (`Smart Ease Photobook Designer` & `Ease Select`) to verify license status, validate machine identity, and handle auto-renewal.
- **Multi-Product Package Management**:
  1. *Smart Ease Photobook Designer* ($2.45/mo recurring)
  2. *Ease Select* ($2.45/mo recurring)
  3. *Smart Ease Photobook Designer + Ease Select Bundle* ($3.00/mo recurring)
  4. *Smart Ease Lifetime Pro Package* (One-time purchase)
- **Payment Gateway Webhook Integrations**:
  - Stripe Checkout & Webhooks (`/api/v1/payments/stripe/webhook`)
  - PayPal Subscriptions & IPN (`/api/v1/payments/paypal/webhook`)
  - Pesapal & Flutterwave regional payment support
- **Subscription Lifecycle Engine**: Automatic status recalculation (`Active`, `Expiring Soon`, `Expired`, `Cancelled`, `Lifetime`) based on UTC expiration timestamps.
- **Dedicated Desktop Admin UI**: Clean, responsive iMac-style administrative window running locally on `http://127.0.0.1:8100/`.

---

## 🚀 Quick Start

### 1. Requirements
- Python 3.10+
- Modern Web Browser (Microsoft Edge, Google Chrome, or Firefox)

### 2. Installation
Clone the repository and install dependencies:
```bash
git clone https://github.com/symonpeter215-ux/smart-Ease-Photobook-designer.git
cd smart-Ease-Photobook-designer
pip install -r requirements.txt
```

### 3. Running the Server

#### Windows 1-Click Desktop Launcher
Double-click `Launch-SubscriptionManager.bat` or run:
```cmd
Launch-SubscriptionManager.bat
```
This automatically starts the background server on port `8100` and opens the desktop admin window.

#### Command-Line
```bash
python server.py 8100
```
Open your browser and navigate to: `http://127.0.0.1:8100`

---

## 🧪 Running Automated Tests

Run the complete test suite verifying database integrity, API routes, KPI math, and license verification:
```bash
python test_subscription_manager.py
```

---

## 🔌 API Endpoints Summary

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/health` | `GET` | Health check and server status |
| `/api/v1/dashboard/metrics` | `GET` | All 8 KPI summary cards |
| `/api/v1/dashboard/revenue-chart` | `GET` | Daily gross revenue breakdown for the current month |
| `/api/v1/users` | `GET` | Search and filter registered users |
| `/api/v1/packages` | `GET` | List all available subscription packages |
| `/api/v1/packages/{id}/price` | `PUT` | Update package pricing |
| `/api/v1/subscriptions` | `GET` | Complete subscription records with live status |
| `/api/v1/client/verify-license` | `POST` | Desktop client license & authorization verification |
| `/api/v1/payments/checkout` | `POST` | Generate checkout session for Stripe / PayPal |
| `/api/v1/payments/stripe/webhook` | `POST` | Stripe incoming webhook handler |
| `/api/v1/payments/paypal/webhook` | `POST` | PayPal IPN incoming webhook handler |

---

## 📁 Project Structure

```
SubscriptionManager/
├── server.py                        # FastAPI / Uvicorn server entry point
├── api_routes.py                    # REST API routes and business logic
├── database.py                      # SQLite database layer with auto-status engine
├── app_identity.py                  # Local client application discovery & sync
├── Launch-SubscriptionManager.bat   # Windows 1-click desktop launcher
├── test_subscription_manager.py     # Automated verification test suite
├── requirements.txt                 # Python dependencies
├── data/
│   └── subscription_manager.db      # SQLite database
├── static/                          # Administrative Dashboard Frontend
│   ├── index.html                   # Admin UI layout
│   ├── app.js                       # Frontend reactive logic & API calls
│   └── styles.css                   # Custom UI design
└── integrations/                    # Client integration SDKs
    ├── README_INTEGRATION.md        # Integration guide
    ├── smart_ease_client.py         # Python client SDK
    └── smart_ease_client.js         # JavaScript client SDK
```

---

## 🛡 License
Proprietary & Confidential - Copyright © 2026 Smart Ease Systems.
