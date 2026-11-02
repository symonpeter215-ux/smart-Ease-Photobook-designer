"""
"New version" emails to customers, sent from the admin's connected Gmail.

The admin starts a campaign (app, version, what's new, download link) from the console. The server
sends it in the background to every customer with a confirmed email who hasn't unsubscribed, at most
DAILY_LIMIT a day (a normal Gmail account allows about 500 and password reset codes need some room).
When the day's limit is reached the campaign waits; the admin presses "Send the rest" on a later day.
Progress is stored in app_settings (MongoDB and SQLite) so a server restart doesn't lose it.
"""

import hashlib
import hmac
import html
import json
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from database import get_db_connection
from mongo_db import get_mongo_db
import gmail_sender

DAILY_LIMIT = 450
_STATE_KEY = "release_mail"
_SECRET_KEY = "release_mail_secret"
_lock = threading.Lock()
_sending = {"active": False}


# ---------- Storage ----------

def _read_setting(key: str) -> Optional[Dict[str, Any]]:
    db = get_mongo_db()
    if db is not None:
        try:
            doc = db["app_settings"].find_one({"_id": key})
            if doc:
                return json.loads(doc["value"]) if "value" in doc else {k: v for k, v in doc.items() if k != "_id"}
        except Exception as e:
            print(f"[ReleaseMail] settings read error: {e}")
    conn = get_db_connection()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)")
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()


def _write_setting(key: str, value: Dict[str, Any]):
    text = json.dumps(value)
    db = get_mongo_db()
    if db is not None:
        try:
            db["app_settings"].replace_one({"_id": key}, {"_id": key, "value": text}, upsert=True)
        except Exception as e:
            print(f"[ReleaseMail] settings write error: {e}")
    conn = get_db_connection()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)")
        with conn:
            conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, text))
    finally:
        conn.close()


def _state() -> Dict[str, Any]:
    return _read_setting(_STATE_KEY) or {}


def _secret() -> str:
    s = _read_setting(_SECRET_KEY)
    if not s or not s.get("secret"):
        s = {"secret": secrets.token_hex(32)}
        _write_setting(_SECRET_KEY, s)
    return s["secret"]


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _sent_today(state: Dict[str, Any]) -> int:
    return int(state.get("day_count", 0)) if state.get("day") == _today() else 0


# ---------- Recipients and unsubscribe ----------

def _ensure_optout_column(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "release_emails_opt_out" not in cols:
        with conn:
            conn.execute("ALTER TABLE users ADD COLUMN release_emails_opt_out INTEGER DEFAULT 0")


def recipients() -> List[Dict[str, str]]:
    """Customers with a confirmed email (Google sign-in or the emailed code) who haven't unsubscribed."""
    conn = get_db_connection()
    try:
        _ensure_optout_column(conn)
        rows = conn.execute(
            "SELECT id, email, full_name FROM users WHERE email IS NOT NULL AND email != '' "
            "AND (email_verified = 1 OR auth_provider = 'google') AND COALESCE(release_emails_opt_out, 0) = 0"
        ).fetchall()
    finally:
        conn.close()
    seen, out = set(), []
    for r in rows:
        email = (r["email"] or "").strip().lower()
        if email and email not in seen:
            seen.add(email)
            out.append({"id": r["id"], "email": email, "name": r["full_name"] or ""})
    return out


def unsubscribe_token(user_id: str) -> str:
    return hmac.new(_secret().encode(), f"unsub:{user_id}".encode(), hashlib.sha256).hexdigest()[:32]


def unsubscribe(user_id: str, token: str) -> bool:
    if not user_id or not hmac.compare_digest(unsubscribe_token(user_id), token or ""):
        return False
    conn = get_db_connection()
    try:
        _ensure_optout_column(conn)
        with conn:
            conn.execute("UPDATE users SET release_emails_opt_out = 1 WHERE id = ?", (user_id,))
    finally:
        conn.close()
    db = get_mongo_db()
    if db is not None:
        try:
            db["users"].update_one({"_id": user_id}, {"$set": {"release_emails_opt_out": 1}})
        except Exception as e:
            print(f"[ReleaseMail] unsubscribe mongo error: {e}")
    return True


# ---------- The email ----------

def _message(campaign: Dict[str, Any], person: Dict[str, str]):
    app = campaign["app"]
    version = campaign["version"]
    url = campaign["url"]
    unsub = f"{campaign['server']}/api/v1/email/unsubscribe?u={person['id']}&t={unsubscribe_token(person['id'])}"
    first = (person["name"] or "").split(" ")[0]
    greeting = f"Hi {first}," if first else "Hi,"
    notes = campaign.get("notes") or ""
    subject = f"{app} {version} is out"
    plain = (f"{greeting}\n\nA new version of {app} ({version}) is ready.\n\n"
             + (f"What's new:\n{notes}\n\n" if notes else "")
             + f"Download it here: {url}\n\nInstall it over your current version; your albums and projects stay as they are.\n\n"
             f"Smart Ease\n\nDon't want update emails? Unsubscribe: {unsub}\n")
    notes_html = "".join(f"<li>{html.escape(line.strip(' -•'))}</li>" for line in notes.splitlines() if line.strip())
    body = f"""<div style="font-family:Segoe UI,Arial,sans-serif;max-width:520px;margin:0 auto;color:#1f2937;line-height:1.5;">
  <p>{html.escape(greeting)}</p>
  <p>A new version of <strong>{html.escape(app)}</strong> ({html.escape(version)}) is ready.</p>
  {f'<p style="margin-bottom:4px;"><strong>What&#39;s new</strong></p><ul style="margin-top:0;">{notes_html}</ul>' if notes_html else ''}
  <p style="margin:24px 0;"><a href="{html.escape(url)}" style="background:#0284c7;color:#ffffff;padding:12px 20px;border-radius:8px;text-decoration:none;font-weight:700;">Download {html.escape(app)} {html.escape(version)}</a></p>
  <p style="font-size:13px;color:#6b7280;">Install it over your current version; your albums and projects stay as they are.</p>
  <p>Smart Ease</p>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0 12px;">
  <p style="font-size:12px;color:#9ca3af;">You're getting this because you have a Smart Ease account.
    <a href="{html.escape(unsub)}" style="color:#9ca3af;">Unsubscribe from update emails</a>.</p>
</div>"""
    return subject, plain, body, unsub


# ---------- Sending ----------

def status() -> Dict[str, Any]:
    state = _state()
    campaign = state.get("campaign")
    total = len(recipients())
    out = {
        "gmailConnected": gmail_sender.is_configured(),
        "recipients": total,
        "dailyLimit": DAILY_LIMIT,
        "sentToday": _sent_today(state),
        "sending": _sending["active"],
        "campaign": None,
    }
    if campaign:
        sent = set(campaign.get("sent") or [])
        remaining = sum(1 for p in recipients() if p["email"] not in sent)
        out["campaign"] = {k: campaign.get(k) for k in ("app", "version", "notes", "url", "createdAt")}
        out["campaign"].update(sent=len(sent), failed=len(campaign.get("failed") or []), remaining=remaining)
    return out


def start(app: str, version: str, notes: str, url: str, server: str, replace: bool = False) -> Dict[str, Any]:
    state = _state()
    old = state.get("campaign")
    if old and not replace and not _finished(old):
        raise ValueError(f"The emails for {old['app']} {old['version']} haven't all gone out yet. Send the rest, or start the new one anyway.")
    state["campaign"] = {"app": app, "version": version, "notes": notes, "url": url, "server": server.rstrip("/"),
                         "createdAt": datetime.now(timezone.utc).isoformat(), "sent": [], "failed": []}
    _write_setting(_STATE_KEY, state)
    return continue_sending()


def _finished(campaign: Dict[str, Any]) -> bool:
    sent = set(campaign.get("sent") or [])
    return all(p["email"] in sent for p in recipients())


def continue_sending() -> Dict[str, Any]:
    """Starts the background sender for today's allowance (no-op if it is already running)."""
    if not gmail_sender.is_configured():
        raise ValueError("Connect Gmail first; update emails are sent from it.")
    state = _state()
    if not state.get("campaign"):
        raise ValueError("There is no update email to send.")
    if _sent_today(state) >= DAILY_LIMIT:
        raise ValueError(f"Today's limit of {DAILY_LIMIT} emails is reached. Send the rest tomorrow.")
    with _lock:
        if not _sending["active"]:
            _sending["active"] = True
            threading.Thread(target=_send_worker, daemon=True).start()
    return status()


def _send_worker():
    try:
        state = _state()
        campaign = state["campaign"]
        sent = list(campaign.get("sent") or [])
        failed = list(campaign.get("failed") or [])
        done = set(sent)
        if state.get("day") != _today():
            state["day"], state["day_count"] = _today(), 0
        for i, person in enumerate(p for p in recipients() if p["email"] not in done):
            if state["day_count"] >= DAILY_LIMIT:
                break
            subject, plain, body, unsub = _message(campaign, person)
            ok = gmail_sender.send(person["email"], subject, plain, body,
                                   headers={"List-Unsubscribe": f"<{unsub}>"})
            state["day_count"] += 1
            # Each address is tried once per campaign, so a bad address never blocks the rest.
            sent.append(person["email"])
            if not ok:
                failed.append(person["email"])
            if i % 10 == 9:
                campaign.update(sent=sent, failed=failed)
                _write_setting(_STATE_KEY, state)
            time.sleep(0.3)  # gentle pace for Gmail
        campaign.update(sent=sent, failed=failed)
        _write_setting(_STATE_KEY, state)
    except Exception as e:
        print(f"[ReleaseMail] sender stopped: {e}")
    finally:
        _sending["active"] = False
