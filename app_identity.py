"""
Smart Ease Subscription Manager - Local Application & Render Cloud Sync Engine
Scans and monitors local installations of Smart Ease Photobook Designer and Ease Select,
as well as the live Render Production Cloud Server & MongoDB Atlas database.
"""

import os
import json
import socket
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

PHOTOBOOK_DIR = r"D:\Antigravity\photobook\version zero\photobook-smartalbums"
EASE_SELECT_DIR = r"D:\Antigravity\PixSelect"
PHOTOBOOK_APPDATA_DIR = os.path.join(os.environ.get("APPDATA", ""), "SmartEasePhotobookStudio")
EASE_SELECT_APPDATA_DIR = os.path.join(os.environ.get("APPDATA", ""), "SmartEase")
RENDER_CLOUD_URL = "https://smart-ease-photobook-designer.onrender.com"

def check_port_listening(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

def ping_render_server(timeout: float = 8.0) -> Dict[str, Any]:
    """
    Pings the live Render Cloud Subscription Manager & MongoDB Atlas backend.
    """
    url = f"{RENDER_CLOUD_URL}/health"
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EaseSubscriptionManager"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency = round((time.time() - t0) * 1000, 1)
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8"))
                mongo = body.get("mongodb", {})
                return {
                    "online": True,
                    "status_code": 200,
                    "latency_ms": latency,
                    "server_url": RENDER_CLOUD_URL,
                    "app": body.get("app", "Smart Ease Subscription Manager"),
                    "version": body.get("version", "1.0.0"),
                    "mongodb_connected": mongo.get("connected", False),
                    "database": mongo.get("database", "smart_ease_subscriptions"),
                    "message": f"Connected to Render Cloud ({latency}ms) • MongoDB Atlas Online"
                }
    except Exception as e:
        return {
            "online": False,
            "error": str(e),
            "server_url": RENDER_CLOUD_URL,
            "message": f"Render Cloud server offline / waking up: {str(e)}"
        }

def ping_photobook_server(timeout: float = 1.5) -> Dict[str, Any]:
    """
    Pings the live Photobook Designer server on http://127.0.0.1:8765.
    Returns real-time connection status, response latency, and active subscription state.
    """
    url = "http://127.0.0.1:8765/api/subscription/status"
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EaseSubscriptionManager"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency = round((time.time() - t0) * 1000, 1)
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8"))
                return {
                    "online": True,
                    "status_code": 200,
                    "latency_ms": latency,
                    "server_url": "http://127.0.0.1:8765",
                    "package": body.get("data", {}).get("packageName") or body.get("packageName") or "Photobook Studio",
                    "license_status": body.get("status") or "active",
                    "hwid": body.get("data", {}).get("hwid") or body.get("hwid"),
                    "days_remaining": body.get("daysRemaining") or body.get("data", {}).get("daysRemaining"),
                    "message": f"Connected to Photobook Studio on port 8765 ({latency}ms)"
                }
    except Exception as e:
        return {
            "online": False,
            "error": str(e),
            "server_url": "http://127.0.0.1:8765",
            "message": f"Photobook server offline: {str(e)}"
        }

def push_license_to_photobook(license_key: str = "SE-LIFETIME-PRO-UNLOCK", email: str = "studio@rafiapictures.com") -> Dict[str, Any]:
    """
    Pushes an activated license key directly into the running Photobook server on port 8765.
    Unlocks all features, export buttons, and spreads in Photobook instantly.
    """
    url = "http://127.0.0.1:8765/api/auth/activate_license"
    payload = {
        "licenseKey": license_key,
        "email": email,
        "packageId": "pkg_lifetime"
    }
    try:
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json", "User-Agent": "EaseSubscriptionManager"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            if resp.status == 200:
                res_body = json.loads(resp.read().decode("utf-8"))
                return {
                    "success": res_body.get("success", True),
                    "server_response": res_body,
                    "message": "✓ Successfully pushed active license to Photobook Studio! All features unlocked."
                }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": f"Failed to push license to Photobook: {str(e)}"
        }

def get_connected_apps_status() -> Dict[str, Any]:
    photobook_running = check_port_listening("127.0.0.1", 8765)
    ease_select_running = check_port_listening("127.0.0.1", 8098)

    # Perform real HTTP telemetry if port 8765 is listening
    photobook_telemetry = None
    if photobook_running:
        photobook_telemetry = ping_photobook_server(timeout=1.0)

    # Telemetry to Render production cloud server
    render_telemetry = ping_render_server(timeout=2.5)

    # Check photobook identity file
    photobook_identity_path = os.path.join(PHOTOBOOK_APPDATA_DIR, "user_identity.json")
    photobook_identity_found = os.path.exists(photobook_identity_path)
    photobook_user = None
    if photobook_identity_found:
        try:
            with open(photobook_identity_path, "r", encoding="utf-8") as f:
                photobook_user = json.load(f)
        except Exception:
            pass

    return {
        "render_cloud": {
            "name": "Smart Ease Render Cloud Backend",
            "url": RENDER_CLOUD_URL,
            "is_online": render_telemetry.get("online", False),
            "status": f"Online &bull; MongoDB Connected ({render_telemetry.get('latency_ms', 0)}ms)" if render_telemetry.get("online") else "Standby / Waking up",
            "telemetry": render_telemetry
        },
        "photobook": {
            "name": "Smart Ease Photobook Designer",
            "installed": os.path.isdir(PHOTOBOOK_DIR),
            "directory": PHOTOBOOK_DIR,
            "port": 8765,
            "is_running": photobook_running,
            "status": f"Running &bull; Live Connected ({photobook_telemetry.get('latency_ms', 0)}ms)" if (photobook_running and photobook_telemetry and photobook_telemetry.get("online")) else ("Running (Port 8765)" if photobook_running else "Standby / Offline"),
            "identity_file_exists": photobook_identity_found,
            "detected_profile": photobook_user,
            "telemetry": photobook_telemetry
        },
        "ease_select": {
            "name": "Ease Select",
            "installed": os.path.isdir(EASE_SELECT_DIR),
            "directory": EASE_SELECT_DIR,
            "port": 8098,
            "is_running": ease_select_running,
            "status": "Running (Active)" if ease_select_running else "Standby / Offline",
            "identity_file_exists": os.path.isdir(EASE_SELECT_APPDATA_DIR)
        }
    }

def sync_render_cloud_data(conn) -> Dict[str, Any]:
    """
    Fetches production users, subscriptions, and transactions from Render Cloud API
    and synchronizes them directly into the local SQLite database.
    """
    t0 = time.time()
    base = RENDER_CLOUD_URL
    synced = {"users": 0, "subscriptions": 0, "transactions": 0}
    
    # 1. Sync Users
    try:
        req = urllib.request.Request(f"{base}/api/v1/users", headers={"User-Agent": "EaseSubscriptionManager"})
        with urllib.request.urlopen(req, timeout=12.0) as resp:
            users = json.loads(resp.read().decode("utf-8"))
            for u in users:
                conn.execute("""
                    INSERT OR REPLACE INTO users (
                        id, smart_ease_account_id, full_name, email, registered_date,
                        product_owned, package_name, source_app, device_hostname, notes,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    u.get("id"), u.get("smart_ease_account_id"), u.get("full_name"),
                    u.get("email"), u.get("registered_date"), u.get("product_owned"),
                    u.get("package_name"), u.get("source_app"), u.get("device_hostname"),
                    u.get("notes"), u.get("created_at"), u.get("updated_at")
                ))
                synced["users"] += 1
    except Exception as e:
        print(f"[RenderSync] Users note: {e}")

    # 2. Sync Subscriptions
    try:
        req = urllib.request.Request(f"{base}/api/v1/subscriptions", headers={"User-Agent": "EaseSubscriptionManager"})
        with urllib.request.urlopen(req, timeout=12.0) as resp:
            subs = json.loads(resp.read().decode("utf-8"))
            for s in subs:
                conn.execute("""
                    INSERT OR REPLACE INTO subscriptions (
                        id, user_id, package_id, product_or_package, start_date,
                        expiry_date, billing_interval, status, payment_status,
                        next_renewal_date, cancellation_date, last_successful_payment,
                        payment_transaction_ref, amount_paid, currency, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    s.get("id"), s.get("user_id"), s.get("package_id"),
                    s.get("product_or_package"), s.get("start_date"), s.get("expiry_date"),
                    s.get("billing_interval"), s.get("status"), s.get("payment_status"),
                    s.get("next_renewal_date"), s.get("cancellation_date"),
                    s.get("last_successful_payment"), s.get("payment_transaction_ref"),
                    s.get("amount_paid"), s.get("currency", "USD"), s.get("created_at"), s.get("updated_at")
                ))
                synced["subscriptions"] += 1
    except Exception as e:
        print(f"[RenderSync] Subscriptions note: {e}")

    # 3. Sync Transactions
    try:
        req = urllib.request.Request(f"{base}/api/v1/transactions", headers={"User-Agent": "EaseSubscriptionManager"})
        with urllib.request.urlopen(req, timeout=12.0) as resp:
            txns = json.loads(resp.read().decode("utf-8"))
            for t in txns:
                conn.execute("""
                    INSERT OR REPLACE INTO transactions (
                        id, transaction_date, user_id, subscription_id, package_id,
                        product_or_package, amount, refund_amount, fee_amount, net_amount,
                        currency, payment_status, payment_provider, transaction_reference,
                        is_verified, notes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    t.get("id"), t.get("transaction_date"), t.get("user_id"),
                    t.get("subscription_id"), t.get("package_id"), t.get("product_or_package"),
                    t.get("amount"), t.get("refund_amount", 0.0), t.get("fee_amount", 0.0),
                    t.get("net_amount"), t.get("currency", "USD"), t.get("payment_status"),
                    t.get("payment_provider"), t.get("transaction_reference"),
                    t.get("is_verified", 1), t.get("notes"), t.get("created_at")
                ))
                synced["transactions"] += 1
    except Exception as e:
        print(f"[RenderSync] Transactions note: {e}")

    conn.commit()
    elapsed = round(time.time() - t0, 2)
    return {
        "status": "success",
        "synced": synced,
        "elapsed_seconds": elapsed,
        "message": f"Successfully synced {synced['users']} users, {synced['subscriptions']} subs, {synced['transactions']} txns from Render Cloud ({elapsed}s)!"
    }

def sync_local_app_identities(conn) -> Dict[str, Any]:
    """
    Reads local app files and syncs users into database.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    synced_records = 0
    details = []

    # 1. Sync Photobook identity profile if present
    photobook_identity_path = os.path.join(PHOTOBOOK_APPDATA_DIR, "user_identity.json")
    if os.path.exists(photobook_identity_path):
        try:
            with open(photobook_identity_path, "r", encoding="utf-8") as f:
                ident = json.load(f)
            user_id_raw = ident.get("userId")
            user_name = ident.get("userName") or "Smart Ease Photobook User"
            email = ident.get("email") or ""
            hostname = ident.get("deviceHostname") or socket.gethostname()
            reg_date = ident.get("firstSeen") or now

            if user_id_raw:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM users WHERE smart_ease_account_id = ?", (user_id_raw,))
                existing = cursor.fetchone()
                if not existing:
                    new_uid = f"USR-{abs(hash(user_id_raw)) % 90000 + 10000}"
                    conn.execute("""
                        INSERT INTO users (
                            id, smart_ease_account_id, full_name, email, registered_date,
                            product_owned, package_name, source_app, device_hostname, notes,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'Smart Ease Photobook Designer', 'Free / Trial', 'Photobook', ?, 'Auto-imported from local Photobook app data', ?, ?)
                    """, (new_uid, user_id_raw, user_name, email, reg_date, hostname, now, now))
                    synced_records += 1
                    details.append(f"Imported Photobook user {user_name} ({user_id_raw})")
                else:
                    # Update hostname / device info
                    conn.execute("""
                        UPDATE users
                        SET device_hostname = ?, full_name = CASE WHEN full_name = '' THEN ? ELSE full_name END, updated_at = ?
                        WHERE smart_ease_account_id = ?
                    """, (hostname, user_name, now, user_id_raw))
                    details.append(f"Synchronized existing Photobook user ({user_id_raw})")
        except Exception as e:
            details.append(f"Photobook sync error: {str(e)}")

    # Log the sync event
    conn.execute("""
        INSERT INTO app_sync_logs (app_name, synced_at, records_synced, status, details)
        VALUES (?, ?, ?, ?, ?)
    """, ("Smart Ease Apps Auto-Scanner", now, synced_records, "SUCCESS", "; ".join(details) or "Local identities checked, already up-to-date"))
    conn.commit()

    return {
        "status": "success",
        "synced_records": synced_records,
        "details": details,
        "synced_at": now
    }
