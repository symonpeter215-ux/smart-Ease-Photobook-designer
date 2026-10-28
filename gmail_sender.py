"""
Sends Smart Ease emails (password reset codes) from the admin's own Gmail through the Gmail API.
The admin connects Gmail once from the admin console; the server keeps the refresh token and
swaps it for short-lived access tokens. HTTPS only, so it works on Render's free plan.
"""

import base64
import os
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any, Dict, Optional

from database import get_db_connection
from mongo_db import get_mongo_db

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.send"
_SETTINGS_KEY = "gmail_sender"
_access: Dict[str, Any] = {"token": None, "expires": 0.0}
_cache: Dict[str, Any] = {"value": None, "loaded": 0.0}


def _client():
    cid = "".join(os.environ.get("GOOGLE_CLIENT_ID", "").split())
    secret = "".join(os.environ.get("GOOGLE_CLIENT_SECRET", "").split())
    return cid, secret


def _ensure_table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)")


def load_config(force: bool = False) -> Optional[Dict[str, Any]]:
    """{'email', 'refresh_token', 'connected_at'} or None. Cached for a minute."""
    if not force and time.time() - _cache["loaded"] < 60:
        return _cache["value"]
    value = None
    db = get_mongo_db()
    if db is not None:
        try:
            doc = db["app_settings"].find_one({"_id": _SETTINGS_KEY})
            if doc and doc.get("refresh_token"):
                value = {k: doc.get(k) for k in ("email", "refresh_token", "connected_at")}
        except Exception as e:
            print(f"[Gmail] settings read error: {e}")
    if value is None:
        import json
        conn = get_db_connection()
        try:
            _ensure_table(conn)
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (_SETTINGS_KEY,)).fetchone()
            if row:
                value = json.loads(row[0])
        finally:
            conn.close()
    _cache.update(value=value, loaded=time.time())
    return value


def save_config(email: str, refresh_token: str, connected_at: str):
    import json
    value = {"email": email, "refresh_token": refresh_token, "connected_at": connected_at}
    db = get_mongo_db()
    if db is not None:
        db["app_settings"].replace_one({"_id": _SETTINGS_KEY}, {"_id": _SETTINGS_KEY, **value}, upsert=True)
    conn = get_db_connection()
    try:
        _ensure_table(conn)
        with conn:
            conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (_SETTINGS_KEY, json.dumps(value)))
    finally:
        conn.close()
    _access.update(token=None, expires=0.0)
    _cache.update(value=value, loaded=time.time())


def clear_config():
    db = get_mongo_db()
    if db is not None:
        db["app_settings"].delete_one({"_id": _SETTINGS_KEY})
    conn = get_db_connection()
    try:
        _ensure_table(conn)
        with conn:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (_SETTINGS_KEY,))
    finally:
        conn.close()
    _access.update(token=None, expires=0.0)
    _cache.update(value=None, loaded=time.time())


def is_configured() -> bool:
    cid, secret = _client()
    try:
        return bool(cid and secret and load_config())
    except Exception as e:
        print(f"[Gmail] config check error: {e}")
        return False


def _access_token(cfg: Dict[str, Any]) -> Optional[str]:
    if _access["token"] and time.time() < _access["expires"] - 60:
        return _access["token"]
    import httpx
    cid, secret = _client()
    resp = httpx.post("https://oauth2.googleapis.com/token", data={
        "client_id": cid, "client_secret": secret,
        "refresh_token": cfg["refresh_token"], "grant_type": "refresh_token",
    }, timeout=15.0)
    if resp.status_code != 200:
        print(f"[Gmail] token refresh failed: {resp.status_code} {resp.text[:300]}")
        return None
    data = resp.json()
    _access.update(token=data["access_token"], expires=time.time() + int(data.get("expires_in", 3600)))
    return _access["token"]


def send(to: str, subject: str, plain_text: str, html_body: str, from_name: str = "Smart Ease") -> bool:
    cfg = load_config()
    if not cfg:
        return False
    try:
        token = _access_token(cfg)
        if not token:
            return False
        msg = MIMEMultipart("alternative")
        msg["To"] = to
        msg["From"] = formataddr((from_name, cfg["email"]))
        msg["Subject"] = subject
        msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        import httpx
        resp = httpx.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                          headers={"Authorization": f"Bearer {token}"}, json={"raw": raw}, timeout=20.0)
        if resp.status_code >= 400:
            print(f"[Gmail] send to {to} failed: {resp.status_code} {resp.text[:300]}")
            return False
        return True
    except Exception as e:
        print(f"[Gmail] send error to {to}: {e}")
        return False
