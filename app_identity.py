"""
Smart Ease Subscription Manager - Local Application Auto-Discovery & Sync Engine
Scans and monitors local installations of Smart Ease Photobook Designer and Ease Select.
"""

import os
import json
import socket
from datetime import datetime, timezone
from typing import Dict, Any, List

PHOTOBOOK_DIR = r"D:\Antigravity\photobook\version zero\photobook-smartalbums"
EASE_SELECT_DIR = r"D:\Antigravity\PixSelect"
PHOTOBOOK_APPDATA_DIR = os.path.join(os.environ.get("APPDATA", ""), "SmartEasePhotobookStudio")
EASE_SELECT_APPDATA_DIR = os.path.join(os.environ.get("APPDATA", ""), "SmartEase")

def check_port_listening(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

def get_connected_apps_status() -> Dict[str, Any]:
    photobook_running = check_port_listening("127.0.0.1", 8765)
    ease_select_running = check_port_listening("127.0.0.1", 8098)

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
        "photobook": {
            "name": "Smart Ease Photobook Designer",
            "installed": os.path.isdir(PHOTOBOOK_DIR),
            "directory": PHOTOBOOK_DIR,
            "port": 8765,
            "is_running": photobook_running,
            "status": "Running (Active)" if photobook_running else "Standby / Offline",
            "identity_file_exists": photobook_identity_found,
            "detected_profile": photobook_user
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
