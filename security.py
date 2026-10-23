"""
Smart Ease Subscription Manager - Security helpers
- Customer/admin session tokens (stored hashed in MongoDB, SQLite fallback for local runs)
- FastAPI dependencies: require_user / require_admin
- LivePay webhook signature verification and transaction status lookups
"""

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx
from fastapi import Header, HTTPException

from database import get_db_connection
from mongo_db import get_mongo_db

SESSION_DAYS = 30

# Accounts allowed to use the admin console (they sign in with their normal Smart Ease account).
ADMIN_EMAILS = {
    e.strip().lower()
    for e in (os.environ.get("ADMIN_EMAILS") or "symonpeter215@gmail.com").split(",")
    if e.strip()
}

LIVEPAY_API_KEY = (
    os.environ.get("LIVEPAY_API_KEY")
    or os.environ.get("LIVEPAY_WEB_KEY")
    or ""
)
LIVEPAY_ACCOUNT_NUMBER = os.environ.get("LIVEPAY_ACCOUNT_NUMBER") or os.environ.get("LIVEPAY_MERCHANT_ID", "")
# LivePay signs webhooks with the account's webhook secret.
LIVEPAY_WEBHOOK_SECRET = os.environ.get("LIVEPAY_WEBHOOK_SECRET") or os.environ.get("LIVEPAY_SECRET_KEY", "")
_base_raw = os.environ.get("LIVEPAY_BASE_URL", "https://livepay.me/api").strip()
LIVEPAY_BASE_URL = "https://livepay.me/api" if ("api.livepay.me" in _base_raw or not _base_raw) else _base_raw.rstrip("/")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ensure_sqlite_sessions(conn: sqlite3.Connection):
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                email TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
        """)


# ==========================================
# Sessions
# ==========================================

def create_session(user_id: str, email: str) -> str:
    """Creates a session and returns the raw token (only its hash is stored)."""
    token = secrets.token_urlsafe(32)
    now = _utcnow()
    doc = {
        "_id": _hash_token(token),
        "user_id": user_id,
        "email": (email or "").lower(),
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=SESSION_DAYS)).isoformat(),
    }
    db = get_mongo_db()
    if db is not None:
        db["sessions"].replace_one({"_id": doc["_id"]}, doc, upsert=True)
    else:
        conn = get_db_connection()
        _ensure_sqlite_sessions(conn)
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, user_id, email, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (doc["_id"], doc["user_id"], doc["email"], doc["created_at"], doc["expires_at"]),
            )
        conn.close()
    return token


def _load_session(token: str) -> Optional[Dict[str, Any]]:
    sid = _hash_token(token)
    db = get_mongo_db()
    if db is not None:
        sess = db["sessions"].find_one({"_id": sid})
    else:
        conn = get_db_connection()
        _ensure_sqlite_sessions(conn)
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        conn.close()
        sess = dict(row) if row else None
    if not sess:
        return None
    try:
        if datetime.fromisoformat(sess["expires_at"]) < _utcnow():
            return None
    except Exception:
        return None
    return sess


def delete_session(token: str):
    sid = _hash_token(token)
    db = get_mongo_db()
    if db is not None:
        db["sessions"].delete_one({"_id": sid})
    else:
        conn = get_db_connection()
        _ensure_sqlite_sessions(conn)
        with conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        conn.close()


def _bearer(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    return authorization.split(" ", 1)[1].strip()


def _load_user(user_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if row:
        return dict(row)
    db = get_mongo_db()
    if db is not None:
        doc = db["users"].find_one({"_id": user_id})
        if doc:
            return dict(doc)
    return None


async def require_user(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """FastAPI dependency: the signed-in customer, from the Authorization: Bearer <token> header."""
    token = _bearer(authorization)
    sess = _load_session(token)
    if not sess:
        raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
    user = _load_user(sess["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="Account not found. Please sign in again.")
    user["_session_token"] = token
    return user


async def require_admin(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """FastAPI dependency: only accounts listed in ADMIN_EMAILS may use admin endpoints."""
    user = await require_user(authorization)
    if (user.get("email") or "").lower() not in ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user


# ==========================================
# LivePay
# ==========================================

def verify_livepay_signature(signature_header: Optional[str], webhook_url: str, payload: Dict[str, Any],
                             max_age_seconds: int = 600) -> bool:
    """
    Verifies LivePay's X-Webhook-Signature header ("t=<timestamp>,v=<hex>").
    Signed string = webhook URL + timestamp + the keys customer_reference, internal_reference
    and status sorted alphabetically, each key immediately followed by its value. HMAC-SHA256.
    """
    if not LIVEPAY_WEBHOOK_SECRET or not signature_header:
        return False
    parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
    timestamp, received = parts.get("t", "").strip(), parts.get("v", "").strip()
    if not timestamp or not received:
        return False
    try:
        if abs(_utcnow().timestamp() - int(timestamp)) > max_age_seconds:
            return False
    except ValueError:
        return False
    signed_fields = {k: payload.get(k, "") for k in ("customer_reference", "internal_reference", "status")}
    message = webhook_url + timestamp + "".join(f"{k}{signed_fields[k]}" for k in sorted(signed_fields))
    expected = hmac.new(LIVEPAY_WEBHOOK_SECRET.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


async def livepay_create_card_checkout(reference: str, amount: float, email: str, description: str,
                                       return_url: Optional[str]) -> Dict[str, Any]:
    if not LIVEPAY_API_KEY or not LIVEPAY_ACCOUNT_NUMBER:
        raise HTTPException(status_code=503, detail="Card payments are not configured on the server.")
    body = {
        "accountNumber": LIVEPAY_ACCOUNT_NUMBER,
        "amount": round(float(amount), 2),
        "currency": "USD",
        "reference": reference,
        "email": email,
        "description": description,
    }
    if return_url:
        body["return_url"] = return_url
    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.post(
            f"{LIVEPAY_BASE_URL}/card-collection",
            json=body,
            headers={"Authorization": f"Bearer {LIVEPAY_API_KEY}", "Content-Type": "application/json"},
        )
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    if resp.status_code >= 400 or not data.get("checkout_url"):
        print(f"[LivePay] card-collection failed ({resp.status_code}): {data}")
        raise HTTPException(status_code=502, detail=data.get("error") or data.get("message") or "Could not start card checkout. Please try again.")
    return data


async def livepay_transaction_status(reference: str, currency: str = "USD") -> Optional[Dict[str, Any]]:
    """Asks LivePay directly for the state of a transaction. Returns None if LivePay can't be reached."""
    if not LIVEPAY_API_KEY or not LIVEPAY_ACCOUNT_NUMBER:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{LIVEPAY_BASE_URL}/transaction-status",
                params={"accountNumber": LIVEPAY_ACCOUNT_NUMBER, "currency": currency, "reference": reference},
                headers={"Authorization": f"Bearer {LIVEPAY_API_KEY}", "Content-Type": "application/json"},
            )
        if resp.status_code >= 400:
            print(f"[LivePay] transaction-status {reference} -> HTTP {resp.status_code}")
            return None
        return resp.json()
    except Exception as e:
        print(f"[LivePay] transaction-status error: {e}")
        return None
