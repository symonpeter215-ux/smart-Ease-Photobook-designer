# CONNECTING SMART EASE PHOTOBOOK DESIGNER & EASE SELECT

This guide explains how your existing applications connect to the **Smart Ease Subscription & Revenue Manager**.

## Overview
- **Subscription Manager Address:** `http://127.0.0.1:8100`
- **Smart Ease Photobook Designer:** `http://127.0.0.1:8765` (`D:\Antigravity\photobook\version zero\photobook-smartalbums`)
- **Ease Select:** `http://127.0.0.1:8098` (`D:\Antigravity\PixSelect`)

---

## 1. Quick Automatic Local Sync (Zero Code Required)
The Subscription Manager includes a built-in local scanner.
Whenever you open the Subscription Manager, click **"Sync Connected Apps"** on the Dashboard or **"Connected Apps"** tab:
1. It inspects `%APPDATA%\SmartEasePhotobookStudio\user_identity.json`
2. It detects the active registered user (such as `Rafia Pictures`, Account ID `a6c0fee1d5422ef8`)
3. It imports or synchronizes their profile, device hostname, and status into the database automatically.

---

## 2. Python Integration (For Backend Servers)

Copy `smart_ease_client.py` into your application's backend directory:

```python
from smart_ease_client import SmartEaseSubscriptionClient

# Initialize client for Photobook Designer
client = SmartEaseSubscriptionClient(product_name="photobook")

# Check if user has valid access
verification = client.verify_license(account_id="a6c0fee1d5422ef8")
if verification.get("has_access"):
    print(f"Access granted! Plan: {verification.get('package_name')}")
else:
    print("User does not have an active subscription or license.")
```

For **Ease Select**:
```python
from smart_ease_client import SmartEaseSubscriptionClient

client = SmartEaseSubscriptionClient(product_name="ease_select")
verification = client.verify_license(account_id="user_account_id_here")
```

---

## 3. Webhook / Payment Gateway Integration

When a customer pays via Stripe, PayPal, Flutterwave, or Pesapal:
Send a `POST` request to `http://127.0.0.1:8100/api/v1/subscriptions`:

```json
{
  "user_id": "USR-10001",
  "package_id": "pkg_photobook",
  "amount": 2.45,
  "payment_provider": "Stripe",
  "transaction_reference": "ch_3PzPhoto123456"
}
```

The Subscription Manager will:
1. Verify the payment status.
2. Grant or extend the customer's subscription.
3. Record the transaction with gross, net, and processing fee amounts.
4. Instantly update all Dashboard KPI cards and Revenue charts.
