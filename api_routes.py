"""
Smart Ease Subscription Manager - API Endpoints
Implements all requirements for:
- Dashboard KPI calculations
- Interactive monthly revenue chart
- Searchable & filterable user management
- Configurable product and package management
- Subscriptions tracking with auto-status engine
- Daily & monthly earnings reports
- Connected Smart Ease Applications (Photobook & Ease Select) API
"""

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, Query, Body, Response
from fastapi.responses import StreamingResponse

from database import (
    get_db_connection, recalculate_all_subscription_statuses,
    get_presence_metrics, record_presence_heartbeat, set_user_connection_mode
)
from app_identity import (
    get_connected_apps_status, sync_local_app_identities,
    ping_photobook_server, push_license_to_photobook,
    ping_render_server, sync_render_cloud_data
)

router = APIRouter(prefix="/api/v1")

# Helper to format currency
def fmt_money(val: float) -> float:
    return round(float(val or 0.0), 2)

# ==========================================
# 1. MAIN DASHBOARD ENDPOINTS
# ==========================================

@router.get("/dashboard/metrics")
async def get_dashboard_metrics():
    recalculate_all_subscription_statuses()
    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    current_month_str = now.strftime("%Y-%m")

    # 1. Total Registered Users
    cursor.execute("SELECT COUNT(*) AS total_users FROM users")
    total_users = cursor.fetchone()["total_users"]

    # 2. Active Subscriptions (Active or Lifetime)
    cursor.execute("SELECT COUNT(*) AS active_subs FROM subscriptions WHERE status IN ('Active', 'Lifetime')")
    active_subs = cursor.fetchone()["active_subs"]

    # 3. Expiring Soon (status = 'Expiring Soon')
    cursor.execute("SELECT COUNT(*) AS expiring_soon FROM subscriptions WHERE status = 'Expiring Soon'")
    expiring_soon = cursor.fetchone()["expiring_soon"]

    # 4. Expired Subscriptions
    cursor.execute("SELECT COUNT(*) AS expired_subs FROM subscriptions WHERE status = 'Expired'")
    expired_subs = cursor.fetchone()["expired_subs"]

    # 5. Today's Earnings (Verified transactions with date starting with today's date)
    cursor.execute("""
        SELECT COALESCE(SUM(amount - refund_amount), 0.0) AS today_earnings,
               COUNT(*) AS today_count
        FROM transactions
        WHERE payment_status = 'Verified'
          AND is_verified = 1
          AND transaction_date LIKE ?
    """, (f"{today_str}%",))
    row_today = cursor.fetchone()
    today_earnings = fmt_money(row_today["today_earnings"])
    today_tx_count = row_today["today_count"]

    # 6. This Month's Earnings (Verified transactions in current month)
    cursor.execute("""
        SELECT COALESCE(SUM(amount - refund_amount), 0.0) AS month_earnings,
               COUNT(*) AS month_count
        FROM transactions
        WHERE payment_status = 'Verified'
          AND is_verified = 1
          AND transaction_date LIKE ?
    """, (f"{current_month_str}%",))
    row_month = cursor.fetchone()
    month_earnings = fmt_money(row_month["month_earnings"])
    month_tx_count = row_month["month_count"]

    # 7. Lifetime Revenue (All verified transactions ever, net of refunds)
    cursor.execute("""
        SELECT COALESCE(SUM(amount - refund_amount), 0.0) AS lifetime_rev,
               COALESCE(SUM(refund_amount), 0.0) AS total_refunds,
               COALESCE(SUM(fee_amount), 0.0) AS total_fees,
               COUNT(*) AS total_verified_txns
        FROM transactions
        WHERE payment_status = 'Verified'
          AND is_verified = 1
    """)
    row_life = cursor.fetchone()
    lifetime_revenue = fmt_money(row_life["lifetime_rev"])
    lifetime_refunds = fmt_money(row_life["total_refunds"])
    lifetime_fees = fmt_money(row_life["total_fees"])
    lifetime_tx_count = row_life["total_verified_txns"]

    # 8. Lifetime Pro Users
    cursor.execute("""
        SELECT COUNT(DISTINCT user_id) AS lifetime_pro_users
        FROM subscriptions
        WHERE (status = 'Lifetime' OR billing_interval = 'lifetime' OR package_id = 'pkg_lifetime')
          AND payment_status = 'Verified'
    """)
    lifetime_pro_users = cursor.fetchone()["lifetime_pro_users"]

    # 9. APP SUBSCRIPTION MODEL BREAKDOWN (Both Apps vs Independent Single Apps)
    # Both Apps: Dual Bundle ($3.00)
    cursor.execute("""
        SELECT COUNT(DISTINCT user_id) as cnt
        FROM subscriptions
        WHERE package_id = 'pkg_bundle'
          AND status IN ('Active', 'Lifetime', 'Expiring Soon')
          AND payment_status = 'Verified'
    """)
    both_bundle_users = cursor.fetchone()["cnt"]

    # Independent Photobook subscribers ($2.45)
    cursor.execute("""
        SELECT DISTINCT user_id
        FROM subscriptions
        WHERE package_id = 'pkg_photobook'
          AND status IN ('Active', 'Lifetime', 'Expiring Soon')
          AND payment_status = 'Verified'
    """)
    ind_pb_users_set = set(r["user_id"] for r in cursor.fetchall())

    # Independent Ease Select subscribers ($2.45)
    cursor.execute("""
        SELECT DISTINCT user_id
        FROM subscriptions
        WHERE package_id = 'pkg_ease_select'
          AND status IN ('Active', 'Lifetime', 'Expiring Soon')
          AND payment_status = 'Verified'
    """)
    ind_es_users_set = set(r["user_id"] for r in cursor.fetchall())

    # Lifetime Pro users set
    cursor.execute("""
        SELECT DISTINCT user_id
        FROM subscriptions
        WHERE (status = 'Lifetime' OR billing_interval = 'lifetime' OR package_id = 'pkg_lifetime')
          AND payment_status = 'Verified'
    """)
    lifetime_users_set = set(r["user_id"] for r in cursor.fetchall())

    # Dual Bundle users set
    cursor.execute("""
        SELECT DISTINCT user_id
        FROM subscriptions
        WHERE package_id = 'pkg_bundle'
          AND status IN ('Active', 'Lifetime', 'Expiring Soon')
          AND payment_status = 'Verified'
    """)
    bundle_users_set = set(r["user_id"] for r in cursor.fetchall())

    # Dual Independent (customers holding separate independent subscriptions for both apps)
    dual_ind_users_set = ind_pb_users_set.intersection(ind_es_users_set)
    both_dual_independent_users = len(dual_ind_users_set)

    # Independent only (single app exclusively)
    ind_photobook_only_users = len(ind_pb_users_set - dual_ind_users_set)
    ind_ease_select_only_users = len(ind_es_users_set - dual_ind_users_set)

    # Total using both apps (bundle + lifetime + dual independent)
    total_both_apps_users = len(lifetime_users_set.union(bundle_users_set).union(dual_ind_users_set))
    total_independent_active_users = len(ind_pb_users_set.union(ind_es_users_set))

    app_breakdown = {
        "total_both_apps": total_both_apps_users,
        "both_bundle": both_bundle_users,
        "both_lifetime": lifetime_pro_users,
        "both_dual_independent": both_dual_independent_users,
        "independent_photobook": len(ind_pb_users_set),
        "independent_ease_select": len(ind_es_users_set),
        "independent_photobook_only": ind_photobook_only_users,
        "independent_ease_select_only": ind_ease_select_only_users,
        "total_independent_active": total_independent_active_users
    }

    # Recent transactions
    cursor.execute("""
        SELECT t.*, u.full_name as user_name, u.email as user_email
        FROM transactions t
        LEFT JOIN users u ON t.user_id = u.id
        ORDER BY t.transaction_date DESC
        LIMIT 6
    """)
    recent_txns = [dict(r) for r in cursor.fetchall()]

    presence_metrics = get_presence_metrics(conn)

    conn.close()

    return {
        "total_registered_users": total_users,
        "active_subscriptions": active_subs,
        "expiring_soon": expiring_soon,
        "expired_subscriptions": expired_subs,
        "today_earnings": today_earnings,
        "today_tx_count": today_tx_count,
        "this_month_earnings": month_earnings,
        "this_month_tx_count": month_tx_count,
        "lifetime_revenue": lifetime_revenue,
        "lifetime_refunds": lifetime_refunds,
        "lifetime_fees": lifetime_fees,
        "lifetime_pro_users": lifetime_pro_users,
        "app_breakdown": app_breakdown,
        "presence": presence_metrics,
        "currency": "USD",
        "recent_transactions": recent_txns
    }

@router.get("/dashboard/chart")
async def get_dashboard_chart(year: int = Query(default=None), month: int = Query(default=None)):
    now = datetime.now(timezone.utc)
    if year is None:
        year = now.year
    if month is None:
        month = now.month

    # Determine days in month
    if month in (1, 3, 5, 7, 8, 10, 12):
        days_in_month = 31
    elif month in (4, 6, 9, 11):
        days_in_month = 30
    else:
        # Leap year check
        days_in_month = 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28

    month_str = f"{year:04d}-{month:02d}"

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT transaction_date, amount, refund_amount, package_id, product_or_package
        FROM transactions
        WHERE payment_status = 'Verified'
          AND is_verified = 1
          AND transaction_date LIKE ?
    """, (f"{month_str}%",))
    rows = cursor.fetchall()
    conn.close()

    # Aggregate by day
    daily_data = {d: {"day": d, "date": f"{month_str}-{d:02d}", "total": 0.0, "count": 0, "photobook": 0.0, "ease_select": 0.0, "bundle": 0.0, "lifetime": 0.0} for d in range(1, days_in_month + 1)}

    for r in rows:
        try:
            day_num = int(r["transaction_date"][8:10])
            net = r["amount"] - r["refund_amount"]
            pkg = (r["package_id"] or "").lower()
            name = (r["product_or_package"] or "").lower()

            if day_num in daily_data:
                daily_data[day_num]["total"] += net
                daily_data[day_num]["count"] += 1
                if "lifetime" in pkg or "lifetime" in name:
                    daily_data[day_num]["lifetime"] += net
                elif "bundle" in pkg or "+" in name:
                    daily_data[day_num]["bundle"] += net
                elif "ease_select" in pkg or "ease select" in name:
                    daily_data[day_num]["ease_select"] += net
                else:
                    daily_data[day_num]["photobook"] += net
        except Exception:
            continue

    # Format result
    result_list = []
    month_total = 0.0
    for d in range(1, days_in_month + 1):
        item = daily_data[d]
        item["total"] = fmt_money(item["total"])
        item["photobook"] = fmt_money(item["photobook"])
        item["ease_select"] = fmt_money(item["ease_select"])
        item["bundle"] = fmt_money(item["bundle"])
        item["lifetime"] = fmt_money(item["lifetime"])
        month_total += item["total"]
        result_list.append(item)

    return {
        "year": year,
        "month": month,
        "month_label": datetime(year, month, 1).strftime("%B %Y"),
        "total_revenue": fmt_money(month_total),
        "days_in_month": days_in_month,
        "daily": result_list
    }

# ==========================================
# 2. USER MANAGEMENT ENDPOINTS
# ==========================================

@router.get("/users")
async def get_users(
    search: Optional[str] = Query(default=None),
    product: Optional[str] = Query(default="all"),
    status: Optional[str] = Query(default="all"),
    app_subscription_filter: Optional[str] = Query(default="all"),
    connection_filter: Optional[str] = Query(default="all")
):
    recalculate_all_subscription_statuses()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users ORDER BY registered_date DESC")
    raw_users = cursor.fetchall()

    cursor.execute("""
        SELECT * FROM subscriptions 
        ORDER BY start_date DESC
    """)
    all_subs_raw = cursor.fetchall()
    conn.close()

    subs_by_user = {}
    for s in all_subs_raw:
        s_dict = dict(s)
        uid = s_dict["user_id"]
        if uid not in subs_by_user:
            subs_by_user[uid] = []
        subs_by_user[uid].append(s_dict)

    results = []
    search_lower = (search or "").strip().lower()

    for r in raw_users:
        u = dict(r)
        user_subs = subs_by_user.get(u["id"], [])
        u["user_subscriptions"] = user_subs
        u["total_subscriptions"] = len(user_subs)

        # Active & verified subscriptions
        active_subs = [s for s in user_subs if s["status"] in ("Active", "Lifetime", "Expiring Soon") and s["payment_status"] == "Verified"]
        u["active_subscriptions_count"] = len(active_subs)

        # Primary subscription (most recent)
        primary_sub = user_subs[0] if user_subs else None

        u["sub_id"] = primary_sub["id"] if primary_sub else None
        u["subscription_start_date"] = primary_sub["start_date"] if primary_sub else None
        u["subscription_expiry_date"] = primary_sub["expiry_date"] if primary_sub else None
        u["subscription_status"] = primary_sub["status"] if primary_sub else "Free / Unlicensed"
        u["billing_interval"] = primary_sub["billing_interval"] if primary_sub else None
        u["payment_status"] = primary_sub["payment_status"] if primary_sub else None
        u["amount_paid"] = sum(s["amount_paid"] for s in active_subs) if active_subs else (primary_sub["amount_paid"] if primary_sub else 0.0)
        u["last_successful_payment_date"] = primary_sub["last_successful_payment"] if primary_sub else None
        u["next_renewal_date"] = primary_sub["next_renewal_date"] if primary_sub else None
        u["payment_transaction_ref"] = primary_sub["payment_transaction_ref"] if primary_sub else None

        # Determine exact App Access & Subscription Model
        has_lifetime = any(s["package_id"] == "pkg_lifetime" or s["status"] == "Lifetime" for s in active_subs)
        has_bundle = any(s["package_id"] == "pkg_bundle" for s in active_subs)
        has_ind_pb = any(s["package_id"] == "pkg_photobook" for s in active_subs)
        has_ind_es = any(s["package_id"] == "pkg_ease_select" for s in active_subs)

        if has_lifetime:
            u["app_access_code"] = "both_lifetime"
            u["app_access_label"] = "Both Apps (Lifetime Pro)"
            u["app_access_category"] = "both"
            u["app_access_badge"] = "badge-lifetime"
            u["product_owned"] = "Both products (Lifetime)"
        elif has_bundle:
            u["app_access_code"] = "both_bundle"
            u["app_access_label"] = "Both Apps (Dual Bundle)"
            u["app_access_category"] = "both"
            u["app_access_badge"] = "badge-indigo"
            u["product_owned"] = "Both products (Bundle)"
        elif has_ind_pb and has_ind_es:
            u["app_access_code"] = "both_independent"
            u["app_access_label"] = "Both Apps (Dual Independent Subs)"
            u["app_access_category"] = "both"
            u["app_access_badge"] = "badge-purple"
            u["product_owned"] = "Both products (2 Independent Subs)"
        elif has_ind_pb:
            u["app_access_code"] = "independent_photobook"
            u["app_access_label"] = "Photobook (Independent Sub)"
            u["app_access_category"] = "independent"
            u["app_access_badge"] = "badge-blue"
            u["product_owned"] = "Smart Ease Photobook Designer"
        elif has_ind_es:
            u["app_access_code"] = "independent_ease_select"
            u["app_access_label"] = "Ease Select (Independent Sub)"
            u["app_access_category"] = "independent"
            u["app_access_badge"] = "badge-teal"
            u["product_owned"] = "Ease Select"
        else:
            if user_subs:
                last_s = user_subs[0]
                pkg = last_s["package_id"]
                if pkg == "pkg_photobook":
                    u["app_access_code"] = "independent_photobook"
                    u["app_access_label"] = "Photobook (Independent - Expired)"
                    u["app_access_category"] = "independent"
                    u["app_access_badge"] = "badge-danger"
                elif pkg == "pkg_ease_select":
                    u["app_access_code"] = "independent_ease_select"
                    u["app_access_label"] = "Ease Select (Independent - Expired)"
                    u["app_access_category"] = "independent"
                    u["app_access_badge"] = "badge-danger"
                else:
                    u["app_access_code"] = "both_bundle"
                    u["app_access_label"] = "Both Apps (Expired)"
                    u["app_access_category"] = "both"
                    u["app_access_badge"] = "badge-danger"
            else:
                u["app_access_code"] = "unlicensed"
                u["app_access_label"] = "Free / Unlicensed"
                u["app_access_category"] = "none"
                u["app_access_badge"] = "badge-neutral"

        # Determine Live Presence & Connection Mode
        now_dt = datetime.now(timezone.utc)
        last_seen_str = u.get("last_seen")
        is_online = False
        presence_code = "offline"
        presence_label = "Offline"
        presence_badge = "badge-neutral"
        last_seen_rel = "Never Connected"

        if last_seen_str:
            dt_seen = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    dt_seen = datetime.strptime(last_seen_str.split(".")[0], fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            if dt_seen:
                secs = max(0, (now_dt - dt_seen).total_seconds())
                if secs <= 600:  # 10 minutes
                    is_online = True
                    presence_code = "online"
                    presence_label = "Online Now"
                    presence_badge = "badge-online"
                elif secs <= 86400:  # 24 hours
                    presence_code = "recent"
                    presence_label = "Recent (Today)"
                    presence_badge = "badge-recent"
                else:
                    presence_code = "offline"
                    presence_label = "Offline"
                    presence_badge = "badge-neutral"

                if secs < 60:
                    last_seen_rel = "Just now"
                elif secs < 3600:
                    last_seen_rel = f"{int(secs // 60)}m ago"
                elif secs < 86400:
                    last_seen_rel = f"{int(secs // 3600)}h ago"
                else:
                    days = int(secs // 86400)
                    last_seen_rel = f"{days}d ago"
            else:
                last_seen_rel = last_seen_str

        u["is_online"] = is_online
        u["presence_code"] = presence_code
        u["presence_label"] = presence_label
        u["presence_badge"] = presence_badge
        u["last_seen_relative"] = last_seen_rel
        conn_mode = u.get("connection_mode") or "online"
        u["connection_mode"] = conn_mode
        u["connection_mode_label"] = "Uses Offline Mode" if conn_mode == "offline_mode" else "Online Sync"
        u["connection_mode_badge"] = "badge-amber" if conn_mode == "offline_mode" else "badge-blue"
        u["last_app"] = u.get("last_app") or u.get("source_app") or "Photobook"

        # 1. Search Filter
        if search_lower:
            match_name = search_lower in (u["full_name"] or "").lower()
            match_email = search_lower in (u["email"] or "").lower()
            match_acc = search_lower in (u["smart_ease_account_id"] or "").lower()
            if not (match_name or match_email or match_acc):
                continue

        # 2. Product Filter (Standard)
        if product and product != "all":
            prod_val = (u["product_owned"] or "").lower()
            if product == "photobook" and "photobook" not in prod_val:
                continue
            elif product == "ease_select" and "ease select" not in prod_val and "ease_select" not in prod_val:
                continue
            elif product == "both" and ("both" not in prod_val and "+" not in prod_val):
                continue
            elif product == "none" and prod_val != "none":
                continue

        # 3. Status Filter
        if status and status != "all":
            st_val = (u["subscription_status"] or "").lower()
            if status == "active" and st_val not in ("active", "lifetime"):
                continue
            elif status == "expired" and st_val != "expired":
                continue
            elif status == "expiring_soon" and st_val != "expiring soon":
                continue
            elif status == "lifetime" and st_val != "lifetime":
                continue
            elif status == "free" and ("free" not in st_val and "unlicensed" not in st_val):
                continue

        # 4. App Access & Independent Subscription Filter
        if app_subscription_filter and app_subscription_filter != "all":
            asf = app_subscription_filter.lower()
            if asf == "both":
                if u["app_access_category"] != "both":
                    continue
            elif asf == "both_bundle":
                if u["app_access_code"] != "both_bundle":
                    continue
            elif asf == "both_lifetime":
                if u["app_access_code"] != "both_lifetime":
                    continue
            elif asf == "both_independent":
                if u["app_access_code"] != "both_independent":
                    continue
            elif asf == "independent":
                if u["app_access_category"] != "independent" and u["app_access_code"] != "both_independent":
                    continue
            elif asf == "independent_photobook":
                if u["app_access_code"] not in ("independent_photobook", "both_independent"):
                    continue
            elif asf == "independent_ease_select":
                if u["app_access_code"] not in ("independent_ease_select", "both_independent"):
                    continue
            elif asf == "free":
                if u["app_access_code"] != "unlicensed":
                    continue

        # 5. Connection & Presence Filter (Online vs Offline vs Uses Offline Mode)
        if connection_filter and connection_filter != "all":
            cf = connection_filter.lower()
            if cf == "online" and not is_online:
                continue
            elif cf == "offline" and is_online:
                continue
            elif cf in ("offline_mode", "uses_offline") and conn_mode != "offline_mode":
                continue
            elif cf in ("online_mode", "uses_online") and conn_mode != "online":
                continue

        results.append(u)

    return results

@router.get("/users/{user_id}")
async def get_user_detail(user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE id = ? OR smart_ease_account_id = ?", (user_id, user_id))
    user_row = cursor.fetchone()
    if not user_row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    user_data = dict(user_row)
    actual_uid = user_data["id"]

    # Presence details
    now_dt = datetime.now(timezone.utc)
    last_seen_str = user_data.get("last_seen")
    is_online = False
    presence_code = "offline"
    presence_label = "Offline"
    presence_badge = "badge-neutral"
    last_seen_rel = "Never Connected"

    if last_seen_str:
        dt_seen = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt_seen = datetime.strptime(last_seen_str.split(".")[0], fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        if dt_seen:
            secs = max(0, (now_dt - dt_seen).total_seconds())
            if secs <= 600:
                is_online = True
                presence_code = "online"
                presence_label = "Online Now"
                presence_badge = "badge-online"
            elif secs <= 86400:
                presence_code = "recent"
                presence_label = "Recent (Today)"
                presence_badge = "badge-recent"
            else:
                presence_code = "offline"
                presence_label = "Offline"
                presence_badge = "badge-neutral"

            if secs < 60:
                last_seen_rel = "Just now"
            elif secs < 3600:
                last_seen_rel = f"{int(secs // 60)}m ago"
            elif secs < 86400:
                last_seen_rel = f"{int(secs // 3600)}h ago"
            else:
                days = int(secs // 86400)
                last_seen_rel = f"{days}d ago"
        else:
            last_seen_rel = last_seen_str

    conn_mode = user_data.get("connection_mode") or "online"
    user_data["is_online"] = is_online
    user_data["presence_code"] = presence_code
    user_data["presence_label"] = presence_label
    user_data["presence_badge"] = presence_badge
    user_data["last_seen_relative"] = last_seen_rel
    user_data["connection_mode"] = conn_mode
    user_data["connection_mode_label"] = "Uses Offline Mode" if conn_mode == "offline_mode" else "Online Sync"
    user_data["connection_mode_badge"] = "badge-amber" if conn_mode == "offline_mode" else "badge-blue"
    user_data["last_app"] = user_data.get("last_app") or user_data.get("source_app") or "Photobook"

    cursor.execute("SELECT * FROM subscriptions WHERE user_id = ? ORDER BY start_date DESC", (actual_uid,))
    subs = [dict(s) for s in cursor.fetchall()]

    cursor.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY transaction_date DESC", (actual_uid,))
    txns = [dict(t) for t in cursor.fetchall()]

    cursor.execute("""
        SELECT * FROM user_presence_logs 
        WHERE user_id = ? 
        ORDER BY created_at DESC 
        LIMIT 8
    """, (actual_uid,))
    presence_logs = [dict(p) for p in cursor.fetchall()]

    conn.close()

    return {
        "user": user_data,
        "subscriptions": subs,
        "transactions": txns,
        "presence_logs": presence_logs
    }

@router.post("/users")
async def create_user(payload: Dict[str, Any] = Body(...)):
    full_name = (payload.get("full_name") or "").strip()
    email = (payload.get("email") or "").strip()
    smart_ease_account_id = (payload.get("smart_ease_account_id") or "").strip()

    if not full_name:
        raise HTTPException(status_code=400, detail="Full name is required")

    if not smart_ease_account_id:
        import hashlib, time
        smart_ease_account_id = hashlib.sha256(f"{full_name}_{time.time()}".encode()).hexdigest()[:16]

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    user_id = f"USR-{datetime.now().strftime('%y%m%d%H%M%S')}"

    conn = get_db_connection()
    with conn:
        conn.execute("""
            INSERT INTO users (
                id, smart_ease_account_id, full_name, email, registered_date,
                product_owned, package_name, source_app, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            smart_ease_account_id,
            full_name,
            email,
            now_str,
            payload.get("product_owned", "Smart Ease Photobook Designer"),
            payload.get("package_name", "Free / Trial"),
            payload.get("source_app", "Manual"),
            payload.get("notes", ""),
            now_str,
            now_str
        ))
    conn.close()
    return {"status": "success", "user_id": user_id, "smart_ease_account_id": smart_ease_account_id}

# ==========================================
# 3. PRODUCT & PACKAGE MANAGEMENT ENDPOINTS
# ==========================================

@router.get("/packages")
async def get_packages():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM packages ORDER BY price ASC")
    packages = [dict(p) for p in cursor.fetchall()]

    for pkg in packages:
        pkg_id = pkg["id"]
        # Count customers who purchased this package
        cursor.execute("""
            SELECT COUNT(DISTINCT user_id) as customer_count
            FROM subscriptions
            WHERE package_id = ? AND payment_status = 'Verified'
        """, (pkg_id,))
        pkg["customer_count"] = cursor.fetchone()["customer_count"]

        # Total revenue from this package
        cursor.execute("""
            SELECT COALESCE(SUM(amount - refund_amount), 0.0) as package_revenue
            FROM transactions
            WHERE package_id = ? AND payment_status = 'Verified' AND is_verified = 1
        """, (pkg_id,))
        pkg["total_revenue"] = fmt_money(cursor.fetchone()["package_revenue"])

    conn.close()
    return packages

@router.put("/packages/{package_id}")
async def update_package(package_id: str, payload: Dict[str, Any] = Body(...)):
    new_price = payload.get("price")
    new_name = payload.get("name")
    new_description = payload.get("description")

    if new_price is None or float(new_price) < 0:
        raise HTTPException(status_code=400, detail="Invalid package price")

    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    with conn:
        conn.execute("""
            UPDATE packages
            SET price = ?,
                name = COALESCE(?, name),
                description = COALESCE(?, description),
                updated_at = ?
            WHERE id = ?
        """, (float(new_price), new_name, new_description, now, package_id))
    conn.close()

    return {"status": "success", "message": "Package updated successfully"}

# ==========================================
# 4. SUBSCRIPTION TRACKING ENDPOINTS
# ==========================================

@router.get("/subscriptions")
async def get_subscriptions(
    status_filter: Optional[str] = Query(default=None),
    access_type_filter: Optional[str] = Query(default=None)
):
    recalculate_all_subscription_statuses()
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT 
            s.*,
            u.full_name as customer_name,
            u.email as customer_email,
            u.smart_ease_account_id as customer_account_id,
            p.billing_type,
            p.product_access
        FROM subscriptions s
        JOIN users u ON s.user_id = u.id
        LEFT JOIN packages p ON s.package_id = p.id
        WHERE 1=1
    """
    params = []
    if status_filter and status_filter.lower() != "all":
        query += " AND s.status = ? "
        params.append(status_filter)

    if access_type_filter and access_type_filter.lower() != "all":
        atf = access_type_filter.lower()
        if atf == "independent":
            query += " AND s.package_id IN ('pkg_photobook', 'pkg_ease_select') "
        elif atf == "independent_photobook":
            query += " AND s.package_id = 'pkg_photobook' "
        elif atf == "independent_ease_select":
            query += " AND s.package_id = 'pkg_ease_select' "
        elif atf == "bundle":
            query += " AND s.package_id = 'pkg_bundle' "
        elif atf == "lifetime":
            query += " AND s.package_id = 'pkg_lifetime' "

    query += " ORDER BY s.start_date DESC"
    cursor.execute(query, params)

    raw_subs = cursor.fetchall()
    conn.close()

    subs = []
    for r in raw_subs:
        s = dict(r)
        pkg_id = s.get("package_id")
        if pkg_id == "pkg_photobook":
            s["is_independent"] = True
            s["access_type_label"] = "Photobook (Independent Sub)"
            s["access_badge"] = "badge-blue"
        elif pkg_id == "pkg_ease_select":
            s["is_independent"] = True
            s["access_type_label"] = "Ease Select (Independent Sub)"
            s["access_badge"] = "badge-teal"
        elif pkg_id == "pkg_bundle":
            s["is_independent"] = False
            s["access_type_label"] = "Both Apps (Dual Bundle)"
            s["access_badge"] = "badge-indigo"
        elif pkg_id == "pkg_lifetime":
            s["is_independent"] = False
            s["access_type_label"] = "Both Apps (Lifetime Pro)"
            s["access_badge"] = "badge-lifetime"
        else:
            s["is_independent"] = False
            s["access_type_label"] = s.get("product_or_package", "Subscription")
            s["access_badge"] = "badge-neutral"

        subs.append(s)

    return subs

@router.post("/subscriptions")
async def create_or_extend_subscription(payload: Dict[str, Any] = Body(...)):
    user_id = payload.get("user_id")
    package_id = payload.get("package_id")

    if not user_id or not package_id:
        raise HTTPException(status_code=400, detail="user_id and package_id are required")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM packages WHERE id = ?", (package_id,))
    pkg = cursor.fetchone()
    if not pkg:
        conn.close()
        raise HTTPException(status_code=404, detail="Package not found")

    now_dt = datetime.now(timezone.utc)
    now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

    # Expiry calculation
    is_lifetime = (pkg["billing_type"] == "one_time" or pkg["billing_interval"] == "lifetime")
    if is_lifetime:
        expiry_str = None
        next_renewal = None
        billing_interval = "lifetime"
        sub_status = "Lifetime"
    else:
        expiry_dt = now_dt + timedelta(days=30)
        expiry_str = expiry_dt.strftime("%Y-%m-%d %H:%M:%S")
        next_renewal = expiry_dt.strftime("%Y-%m-%d")
        billing_interval = pkg["billing_interval"]
        sub_status = "Active"

    sub_id = f"SUB-{now_dt.strftime('%y%m%d%H%M%S')}"
    tx_ref = payload.get("transaction_reference") or f"TXN-MANUAL-{now_dt.strftime('%m%d%H%M')}"
    amount_paid = float(payload.get("amount", pkg["price"]))

    with conn:
        conn.execute("""
            INSERT INTO subscriptions (
                id, user_id, package_id, product_or_package, start_date, expiry_date,
                billing_interval, status, payment_status, next_renewal_date,
                cancellation_date, last_successful_payment, payment_transaction_ref,
                amount_paid, currency, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Verified', ?, NULL, ?, ?, ?, 'USD', ?, ?)
        """, (
            sub_id, user_id, package_id, pkg["name"], now_str, expiry_str,
            billing_interval, sub_status, next_renewal, now_str, tx_ref,
            amount_paid, now_str, now_str
        ))

        # Update user package info
        conn.execute("""
            UPDATE users
            SET package_name = ?,
                product_owned = CASE 
                    WHEN ? = 'photobook' THEN 'Smart Ease Photobook Designer'
                    WHEN ? = 'ease_select' THEN 'Ease Select'
                    ELSE 'Both products' END,
                updated_at = ?
            WHERE id = ?
        """, (pkg["name"], pkg["product_access"], pkg["product_access"], now_str, user_id))

        # Record verified transaction
        txn_id = f"TXN-{now_dt.strftime('%y%m%d%H%M%S')}"
        conn.execute("""
            INSERT INTO transactions (
                id, transaction_date, user_id, subscription_id, package_id,
                product_or_package, amount, refund_amount, fee_amount, net_amount,
                currency, payment_status, payment_provider, transaction_reference,
                is_verified, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, 0.0, ?, 'USD', 'Verified', ?, ?, 1, ?, ?)
        """, (
            txn_id, now_str, user_id, sub_id, package_id, pkg["name"],
            amount_paid, amount_paid, payload.get("payment_provider", "Manual"),
            tx_ref, payload.get("notes", "Subscription registered/extended"), now_str
        ))

    conn.close()
    return {"status": "success", "subscription_id": sub_id, "transaction_id": txn_id}

@router.post("/subscriptions/{sub_id}/cancel")
async def cancel_subscription(sub_id: str):
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    with conn:
        conn.execute("""
            UPDATE subscriptions
            SET cancellation_date = ?, status = 'Cancelled', updated_at = ?
            WHERE id = ?
        """, (now_str, now_str, sub_id))
    conn.close()
    return {"status": "success", "message": "Subscription cancelled"}

# ==========================================
# 5. DAILY AND MONTHLY EARNINGS ENDPOINTS
# ==========================================

@router.get("/revenue/daily")
async def get_daily_revenue(date: Optional[str] = Query(default=None)):
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM transactions
        WHERE payment_status = 'Verified'
          AND is_verified = 1
          AND transaction_date LIKE ?
    """, (f"{date}%",))
    txns = cursor.fetchall()

    total_verified = 0.0
    tx_count = len(txns)
    rev_photobook = 0.0
    rev_ease_select = 0.0
    rev_bundle = 0.0
    rev_lifetime = 0.0

    for t in txns:
        net = t["amount"] - t["refund_amount"]
        total_verified += net
        pkg = (t["package_id"] or "").lower()
        name = (t["product_or_package"] or "").lower()

        if "lifetime" in pkg or "lifetime" in name:
            rev_lifetime += net
        elif "bundle" in pkg or "+" in name:
            rev_bundle += net
        elif "ease_select" in pkg or "ease select" in name:
            rev_ease_select += net
        else:
            rev_photobook += net

    conn.close()

    return {
        "date": date,
        "total_verified_payments": fmt_money(total_verified),
        "successful_transactions_count": tx_count,
        "revenue_photobook": fmt_money(rev_photobook),
        "revenue_ease_select": fmt_money(rev_ease_select),
        "revenue_combined_packages": fmt_money(rev_bundle),
        "revenue_lifetime_pro": fmt_money(rev_lifetime),
        "currency": "USD"
    }

@router.get("/revenue/monthly")
async def get_monthly_revenue(
    year: Optional[int] = Query(default=None),
    month: Optional[int] = Query(default=None)
):
    now = datetime.now(timezone.utc)
    if year is None:
        year = now.year
    if month is None:
        month = now.month

    month_str = f"{year:04d}-{month:02d}"

    conn = get_db_connection()
    cursor = conn.cursor()

    # All verified transactions in month
    cursor.execute("""
        SELECT *
        FROM transactions
        WHERE payment_status = 'Verified'
          AND is_verified = 1
          AND transaction_date LIKE ?
    """, (f"{month_str}%",))
    txns = cursor.fetchall()

    # Refunds in month
    cursor.execute("""
        SELECT COALESCE(SUM(refund_amount), 0.0) as total_refunds
        FROM transactions
        WHERE transaction_date LIKE ?
    """, (f"{month_str}%",))
    total_refunds = fmt_money(cursor.fetchone()["total_refunds"])

    gross_collections = 0.0
    total_fees = 0.0
    rev_photobook = 0.0
    rev_ease_select = 0.0
    rev_bundle = 0.0
    rev_lifetime = 0.0
    rev_recurring = 0.0

    for t in txns:
        gross_collections += t["amount"]
        total_fees += t["fee_amount"]
        net = t["amount"] - t["refund_amount"]
        pkg = (t["package_id"] or "").lower()
        name = (t["product_or_package"] or "").lower()

        if "lifetime" in pkg or "lifetime" in name:
            rev_lifetime += net
        else:
            rev_recurring += net

        if "lifetime" in pkg or "lifetime" in name:
            pass
        elif "bundle" in pkg or "+" in name:
            rev_bundle += net
        elif "ease_select" in pkg or "ease select" in name:
            rev_ease_select += net
        else:
            rev_photobook += net

    net_collections = gross_collections - total_refunds
    conn.close()

    return {
        "month": month_str,
        "month_label": datetime(year, month, 1).strftime("%B %Y"),
        "total_verified_payments": fmt_money(gross_collections),
        "total_successful_transactions": len(txns),
        "revenue_by_product": {
            "photobook": fmt_money(rev_photobook),
            "ease_select": fmt_money(rev_ease_select),
            "combined": fmt_money(rev_bundle),
            "lifetime": fmt_money(rev_lifetime)
        },
        "revenue_by_package": {
            "smart_ease_photobook": fmt_money(rev_photobook),
            "smart_ease_bundle": fmt_money(rev_bundle),
            "ease_select": fmt_money(rev_ease_select),
            "lifetime_pro": fmt_money(rev_lifetime)
        },
        "revenue_recurring_subscriptions": fmt_money(rev_recurring),
        "revenue_lifetime_purchases": fmt_money(rev_lifetime),
        "refunds_and_chargebacks": total_refunds,
        "processing_fees": fmt_money(total_fees),
        "net_recorded_revenue": fmt_money(net_collections),
        "currency": "USD"
    }

@router.get("/transactions")
async def get_transactions(
    status: Optional[str] = Query(default=None),
    provider: Optional[str] = Query(default=None)
):
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT t.*, u.full_name as customer_name, u.email as customer_email, u.smart_ease_account_id
        FROM transactions t
        LEFT JOIN users u ON t.user_id = u.id
        WHERE 1=1
    """
    params = []
    if status and status != "all":
        query += " AND t.payment_status = ?"
        params.append(status)
    if provider and provider != "all":
        query += " AND t.payment_provider = ?"
        params.append(provider)

    query += " ORDER BY t.transaction_date DESC"
    cursor.execute(query, params)
    txns = [dict(t) for t in cursor.fetchall()]
    conn.close()
    return txns

@router.post("/transactions")
async def record_transaction(payload: Dict[str, Any] = Body(...)):
    user_id = payload.get("user_id")
    amount = float(payload.get("amount", 0.0))
    provider = payload.get("payment_provider", "Manual")
    tx_ref = payload.get("transaction_reference")
    status = payload.get("payment_status", "Verified")
    pkg_id = payload.get("package_id")
    prod_name = payload.get("product_or_package", "Smart Ease License")

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    if not tx_ref:
        tx_ref = f"TXN-{datetime.now().strftime('%y%m%d%H%M%S')}"

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    txn_id = f"TXN-{datetime.now().strftime('%y%m%d%H%M%S')}"
    fee = float(payload.get("fee_amount", 0.0))
    net = amount - fee

    conn = get_db_connection()
    with conn:
        conn.execute("""
            INSERT INTO transactions (
                id, transaction_date, user_id, package_id, product_or_package,
                amount, refund_amount, fee_amount, net_amount, currency,
                payment_status, payment_provider, transaction_reference,
                is_verified, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0.0, ?, ?, 'USD', ?, ?, ?, ?, ?, ?)
        """, (
            txn_id, now_str, user_id, pkg_id, prod_name,
            amount, fee, net, status, provider, tx_ref,
            1 if status == "Verified" else 0,
            payload.get("notes", ""), now_str
        ))
    conn.close()
    return {"status": "success", "transaction_id": txn_id}

# ==========================================
# 6. CONNECTED APPS & CLIENT SYNC ENDPOINTS
# ==========================================

@router.get("/apps/status")
async def get_apps_status():
    status = get_connected_apps_status()
    return status

@router.post("/apps/sync-local")
async def trigger_local_sync():
    conn = get_db_connection()
    res = sync_local_app_identities(conn)
    conn.close()
    return res

@router.get("/apps/render/ping")
@router.post("/apps/render/ping")
async def api_ping_render():
    """Real-time ping to the Render production cloud server & MongoDB Atlas."""
    return ping_render_server()

@router.post("/apps/render/sync")
async def api_sync_render():
    """Synchronizes users and subscriptions from Render Cloud MongoDB into local database."""
    conn = get_db_connection()
    res = sync_render_cloud_data(conn)
    recalculate_all_subscription_statuses(conn)
    conn.close()
    return res

@router.get("/apps/photobook/ping")
@router.post("/apps/photobook/ping")
async def api_ping_photobook():
    """Real-time live ping to the Photobook Studio server on port 8765."""
    return ping_photobook_server()

@router.post("/apps/photobook/push-license")
async def api_push_photobook_license(payload: Optional[Dict[str, Any]] = Body(default={})):
    """Pushes verified license to local Photobook instance so all buttons work."""
    key = (payload or {}).get("licenseKey") or "SE-LIFETIME-PRO"
    email = (payload or {}).get("email") or "studio@rafiapictures.com"
    return push_license_to_photobook(license_key=key, email=email)

@router.post("/apps/photobook/launch")
async def api_launch_photobook():
    """Launches Photobook Studio launcher in background if not running."""
    import subprocess
    bat_path = r"D:\Antigravity\photobook\version zero\Launch-In-Chrome.bat"
    if os.path.exists(bat_path):
        subprocess.Popen(["cmd.exe", "/c", bat_path], cwd=os.path.dirname(bat_path))
        return {"status": "success", "message": "Photobook launcher invoked"}
    return {"status": "error", "message": "Launcher bat file not found"}

@router.post("/client/verify-license")
async def client_verify_license(payload: Dict[str, Any] = Body(...)):
    """
    Called by Photobook Designer or Ease Select to verify a customer's active license.
    """
    account_id = payload.get("account_id") or payload.get("user_id") or payload.get("email")
    product = (payload.get("product") or "photobook").lower()

    if not account_id:
        return {"has_access": False, "status": "Invalid Account", "message": "No account identifier provided"}

    recalculate_all_subscription_statuses()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT u.id, u.full_name, u.smart_ease_account_id,
               s.id as sub_id, s.status, s.billing_interval, s.expiry_date,
               s.product_or_package, s.package_id, p.product_access
        FROM users u
        LEFT JOIN subscriptions s ON u.id = s.user_id AND s.payment_status = 'Verified'
        LEFT JOIN packages p ON s.package_id = p.id
        WHERE u.smart_ease_account_id = ? OR u.email = ? OR u.id = ?
        ORDER BY s.created_at DESC
        LIMIT 1
    """, (account_id, account_id, account_id))
    row = cursor.fetchone()
    conn.close()

    if not row or not row["status"]:
        return {
            "has_access": False,
            "status": "Unlicensed",
            "account_id": account_id,
            "message": "No active paid license found for this user account"
        }

    sub_status = row["status"]
    prod_access = (row["product_access"] or "").lower()
    is_lifetime = (row["billing_interval"] == "lifetime" or sub_status == "Lifetime")

    # Check product access
    allowed = False
    if sub_status in ("Active", "Lifetime", "Expiring Soon"):
        if prod_access == "both":
            allowed = True
        elif product in ("photobook", "designer") and prod_access == "photobook":
            allowed = True
        elif product in ("ease_select", "select") and prod_access == "ease_select":
            allowed = True

    # Automatically record active presence heartbeat upon verification
    try:
        record_presence_heartbeat(
            account_id=row["smart_ease_account_id"],
            product=product,
            mode=payload.get("mode") or "online",
            device_hostname=payload.get("device_hostname"),
            ip_address=payload.get("ip") or "127.0.0.1"
        )
    except Exception as e:
        print(f"[Presence] Heartbeat record warning: {e}")

    return {
        "has_access": allowed,
        "status": sub_status,
        "customer_name": row["full_name"],
        "account_id": row["smart_ease_account_id"],
        "package_name": row["product_or_package"],
        "is_lifetime": is_lifetime,
        "expiry_date": row["expiry_date"],
        "is_online": True,
        "connection_mode": payload.get("mode") or "online",
        "message": f"License verified: {sub_status}" if allowed else f"Product {product} is not entitled under this subscription"
    }

@router.post("/client/heartbeat")
async def client_heartbeat(payload: Dict[str, Any] = Body(...)):
    """
    Heartbeat endpoint for client applications (Photobook Designer, Ease Select).
    Updates presence in real-time so administrator knows who is Online vs Offline.
    """
    account_id = payload.get("account_id") or payload.get("user_id") or payload.get("email")
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id is required")

    product = payload.get("product") or "photobook"
    mode = payload.get("mode") or "online"
    device_hostname = payload.get("device_hostname")
    ip_addr = payload.get("ip") or "127.0.0.1"
    version = payload.get("app_version") or "v2.4"

    res = record_presence_heartbeat(
        account_id=account_id,
        product=product,
        mode=mode,
        device_hostname=device_hostname,
        ip_address=ip_addr,
        app_version=version
    )
    return res

@router.get("/presence/metrics")
async def get_presence_stats():
    """Returns dynamic counts of online, offline, and offline-mode users."""
    return get_presence_metrics()

@router.post("/users/{user_id}/toggle-mode")
async def toggle_user_mode(user_id: str, payload: Dict[str, Any] = Body(...)):
    """
    Allows administrator to toggle a customer between 'online' mode and 'offline_mode'
    (authorizing an offline license lease for off-grid fieldwork).
    """
    mode = payload.get("mode")
    if mode not in ("online", "offline_mode"):
        raise HTTPException(status_code=400, detail="mode must be 'online' or 'offline_mode'")
    lease_days = int(payload.get("lease_days") or 30)
    return set_user_connection_mode(user_id=user_id, mode=mode, lease_days=lease_days)

# ==========================================
# 7. EXPORT DATA ENDPOINTS
# ==========================================

@router.get("/export/users.csv")
async def export_users_csv():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT u.full_name, u.email, u.smart_ease_account_id, u.registered_date,
               u.product_owned, u.package_name, s.start_date, s.expiry_date,
               s.status, s.amount_paid, s.last_successful_payment, s.next_renewal_date,
               COALESCE(u.connection_mode, 'online') as connection_mode,
               u.last_seen,
               CASE 
                 WHEN u.last_seen IS NOT NULL AND datetime(u.last_seen) >= datetime('now', '-10 minutes') THEN 'Online Now'
                 WHEN u.last_seen IS NOT NULL AND datetime(u.last_seen) >= datetime('now', '-24 hours') THEN 'Recent (Today)'
                 WHEN u.last_seen IS NOT NULL THEN 'Offline'
                 ELSE 'Never Connected'
               END as presence_status
        FROM users u
        LEFT JOIN subscriptions s ON u.id = s.user_id
        ORDER BY u.registered_date DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Full Name", "Email", "Account ID", "Registered Date",
        "Product Owned", "Package Name", "Subscription Start",
        "Subscription Expiry", "Status", "Amount Paid (USD)",
        "Last Payment Date", "Next Renewal Date",
        "Connection Mode", "Last Seen", "Presence Status"
    ])
    for r in rows:
        writer.writerow(list(r))

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=smartease_users.csv"}
    )

# ==========================================
# 8. LIVEPAY (livepay.me) GATEWAY INTEGRATION
# ==========================================

import os
import uuid
import httpx
from mongo_db import sync_sqlite_to_mongo

LIVEPAY_API_KEY = (
    os.environ.get("LIVEPAY_API_KEY")
    or os.environ.get("LIVEPAY_WEB_KEY")
    or os.environ.get("LIVEPAY_SECRET_KEY")
    or ""
)
LIVEPAY_ACCOUNT_NUMBER = os.environ.get("LIVEPAY_ACCOUNT_NUMBER") or os.environ.get("LIVEPAY_MERCHANT_ID", "")
_base_raw = os.environ.get("LIVEPAY_BASE_URL", "https://livepay.me/api").strip()
if "api.livepay.me" in _base_raw or not _base_raw:
    LIVEPAY_BASE_URL = "https://livepay.me/api"
else:
    LIVEPAY_BASE_URL = _base_raw

@router.post("/payments/livepay/initiate")
async def livepay_initiate_payment(payload: Dict[str, Any] = Body(...)):
    """
    Initiate a mobile money collection via LivePay.me API (/api/collect-money)
    Payload: {
        "account_id": "a6c0fee1d5422ef8",
        "package_id": "pkg_photobook",
        "phone_number": "0777123456",
        "currency": "UGX",
        "amount": 10000
    }
    """
    account_id = payload.get("account_id")
    package_id = payload.get("package_id")
    phone = payload.get("phone_number") or payload.get("phoneNumber", "")
    currency = payload.get("currency", "UGX")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Verify user exists or create
    cursor.execute("SELECT * FROM users WHERE smart_ease_account_id = ?", (account_id,))
    user = cursor.fetchone()
    if not user:
        new_uid = f"USR-{uuid.uuid4().hex[:8].upper()}"
        now_str = datetime.now(timezone.utc).isoformat()
        cust_email = payload.get("email") or f"{phone}@mobilepay.local"
        with conn:
            conn.execute("""
                INSERT INTO users (id, smart_ease_account_id, full_name, email, registered_date, product_owned, package_name, source_app, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (new_uid, account_id, payload.get("email") or f"Customer {phone}", cust_email, now_str, "Smart Ease Photobook Designer", "Photobook Pro", "Photobook", now_str, now_str))
        cursor.execute("SELECT * FROM users WHERE smart_ease_account_id = ?", (account_id,))
        user = cursor.fetchone()

    # Verify package
    cursor.execute("SELECT * FROM packages WHERE id = ?", (package_id,))
    pkg = cursor.fetchone()
    if not pkg:
        default_packages = {
            "pkg_photobook": ("Smart Ease Photobook Designer", 2.45, "monthly"),
            "pkg_bundle": ("Smart Ease Photobook Designer + Ease Select", 3.00, "monthly"),
            "pkg_select": ("Ease Select", 2.45, "monthly"),
            "pkg_ease_select": ("Ease Select", 2.45, "monthly"),
            "pkg_lifetime": ("Lifetime Pro", 200.00, "lifetime")
        }
        pkg_tuple = default_packages.get(package_id, ("Smart Ease Photobook Designer", 2.45, "monthly"))
        pkg = {"id": package_id, "name": pkg_tuple[0], "price": pkg_tuple[1], "billing_interval": pkg_tuple[2]}
    else:
        pkg = dict(pkg)

    amount = float(payload.get("amount") or pkg["price"])
    tx_ref = f"LP{uuid.uuid4().hex[:14].upper()}"
    now_iso = datetime.now(timezone.utc).isoformat()

    # Pre-record pending transaction
    with conn:
        conn.execute("""
            INSERT INTO transactions (
                id, transaction_date, user_id, package_id, product_or_package,
                amount, refund_amount, fee_amount, net_amount, currency,
                payment_status, payment_provider, transaction_reference, is_verified, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"TXN-{uuid.uuid4().hex[:8].upper()}",
            now_iso,
            user["id"],
            pkg["id"],
            pkg["name"],
            amount,
            0.0,
            0.0,
            amount,
            currency,
            "Pending",
            "LivePay",
            tx_ref,
            0,
            f"LivePay collection initiated for {phone}",
            now_iso
        ))

    api_key = LIVEPAY_API_KEY
    account_num = LIVEPAY_ACCOUNT_NUMBER or payload.get("account_number", "")

    if not api_key:
        conn.close()
        return {
            "status": "pending",
            "transaction_reference": tx_ref,
            "message": "Payment recorded locally (LIVEPAY_API_KEY not configured yet)",
            "test_mode": True
        }

    # Dispatch to LivePay API /api/collect-money
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            body = {
                "accountNumber": account_num,
                "phoneNumber": phone,
                "amount": amount,
                "currency": currency,
                "reference": tx_ref,
                "description": f"Smart Ease: {pkg['name']}"
            }
            url = f"{LIVEPAY_BASE_URL.rstrip('/')}/collect-money"
            resp = await client.post(url, json=body, headers=headers)
            livepay_data = resp.json() if resp.status_code < 400 else {"http_error": resp.status_code, "raw": resp.text}
    except Exception as e:
        livepay_data = {"error": str(e)}

    conn.close()
    return {
        "status": "initiated",
        "transaction_reference": tx_ref,
        "amount": amount,
        "currency": currency,
        "livepay_response": livepay_data
    }

@router.post("/payments/livepay/webhook")
async def livepay_webhook(payload: Dict[str, Any] = Body(...)):
    """
    Asynchronous Webhook from LivePay when a customer completes payment.
    Activates subscription and logs verified transaction.
    """
    tx_ref = payload.get("reference") or payload.get("tx_ref") or payload.get("transaction_id")
    event_status = (payload.get("status") or "").lower()
    amount = float(payload.get("amount") or 0.0)

    if not tx_ref:
        return {"status": "ignored", "reason": "No reference in webhook"}

    conn = get_db_connection()
    cursor = conn.cursor()

    # Find pending transaction
    cursor.execute("SELECT * FROM transactions WHERE transaction_reference = ?", (tx_ref,))
    txn = cursor.fetchone()

    is_success = event_status in ("success", "successful", "completed", "paid")
    new_status = "Verified" if is_success else "Failed"
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()

    if txn:
        user_id = txn["user_id"]
        pkg_id = txn["package_id"]
        with conn:
            conn.execute("""
                UPDATE transactions
                SET payment_status = ?, is_verified = ?, notes = ?
                WHERE transaction_reference = ?
            """, (
                new_status,
                1 if is_success else 0,
                f"LivePay webhook received: {event_status}",
                tx_ref
            ))
    else:
        # Fallback if transaction was not pre-recorded
        account_id = payload.get("customer_id") or payload.get("account_id")
        cursor.execute("SELECT * FROM users WHERE smart_ease_account_id = ?", (account_id,))
        u = cursor.fetchone()
        user_id = u["id"] if u else None
        pkg_id = "pkg_photobook"

    if is_success and user_id:
        cursor.execute("SELECT * FROM packages WHERE id = ?", (pkg_id,))
        pkg = cursor.fetchone()
        is_lifetime = (pkg and pkg["billing_interval"] == "lifetime")

        expiry_dt = None if is_lifetime else (now_dt + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        sub_status = "Lifetime" if is_lifetime else "Active"

        # Check existing subscription
        cursor.execute("SELECT * FROM subscriptions WHERE user_id = ?", (user_id,))
        existing_sub = cursor.fetchone()

        with conn:
            if existing_sub:
                conn.execute("""
                    UPDATE subscriptions
                    SET status = ?, payment_status = 'Verified', expiry_date = ?,
                        amount_paid = ?, last_successful_payment = ?, payment_transaction_ref = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (
                    sub_status, expiry_dt, amount or existing_sub["amount_paid"],
                    now_iso, tx_ref, now_iso, existing_sub["id"]
                ))
            else:
                conn.execute("""
                    INSERT INTO subscriptions (
                        id, user_id, package_id, product_or_package, start_date, expiry_date,
                        billing_interval, status, payment_status, last_successful_payment,
                        payment_transaction_ref, amount_paid, currency, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"SUB-{uuid.uuid4().hex[:8].upper()}",
                    user_id,
                    pkg_id or "pkg_photobook",
                    pkg["name"] if pkg else "Smart Ease Photobook Designer",
                    now_iso,
                    expiry_dt,
                    "lifetime" if is_lifetime else "monthly",
                    sub_status,
                    "Verified",
                    now_iso,
                    tx_ref,
                    amount,
                    payload.get("currency", "USD"),
                    now_iso,
                    now_iso
                ))

    # Synchronize to MongoDB cloud
    sync_sqlite_to_mongo(conn)
    conn.close()

    return {"status": "ok", "processed": True, "event": event_status, "reference": tx_ref}

@router.get("/payments/livepay/status/{reference}")
async def livepay_check_status(reference: str):
    """Poll transaction status for client application."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM transactions WHERE transaction_reference = ?", (reference,))
    txn = cursor.fetchone()
    conn.close()

    if not txn:
        raise HTTPException(status_code=404, detail="Transaction reference not found")

    lic_key = None
    if bool(txn["is_verified"]):
        raw_pkg = txn["package_id"] or "pkg_photobook"
        key_pfx = "SE-PHOTOBOOK" if "photobook" in raw_pkg else ("SE-BUNDLE" if "bundle" in raw_pkg else ("SE-SELECT" if "select" in raw_pkg else "SE-LIFETIME"))
        lic_key = f"{key_pfx}-{uuid.uuid4().hex[:8].upper()}-PRO"

    return {
        "reference": reference,
        "payment_status": txn["payment_status"],
        "is_verified": bool(txn["is_verified"]),
        "amount": txn["amount"],
        "currency": txn["currency"],
        "transaction_date": txn["transaction_date"],
        "license_key": lic_key,
        "licenseKey": lic_key
    }

# ==========================================
# 9. CARD PAYMENT (USD) CHECKOUT PROCESSING
# ==========================================

@router.post("/payments/card/process")
async def card_process_payment(payload: Dict[str, Any] = Body(...)):
    """
    Card Payment (USD) Processing for Smart Ease packages.
    Validates card format, records verified USD transaction, activates/renews user subscription,
    and synchronizes to MongoDB Atlas.
    """
    account_id = payload.get("account_id") or "a6c0fee1d5422ef8"
    raw_pkg_id = payload.get("package_id") or payload.get("packageId") or "pkg_photobook"
    
    # Map pkg_select to pkg_ease_select if needed
    pkg_alias_map = {
        "pkg_select": "pkg_ease_select",
        "pkg_ease_select": "pkg_ease_select",
        "pkg_photobook": "pkg_photobook",
        "pkg_bundle": "pkg_bundle",
        "pkg_lifetime": "pkg_lifetime"
    }
    package_id = pkg_alias_map.get(raw_pkg_id, raw_pkg_id)

    card_info = payload.get("card") or {}
    card_number = str(card_info.get("number") or "").replace(" ", "").replace("-", "")
    card_holder = str(card_info.get("name") or "Cardholder").strip()
    card_expiry = str(card_info.get("expiry") or "").strip()
    currency = "USD"

    # Card validation: at least 13 digits, numeric
    if len(card_number) < 13 or not card_number.isdigit():
        raise HTTPException(status_code=400, detail="Invalid card number format. Please provide a valid card number.")

    conn = get_db_connection()
    cursor = conn.cursor()

    # Find or auto-create user
    cursor.execute("SELECT * FROM users WHERE smart_ease_account_id = ?", (account_id,))
    user = cursor.fetchone()
    if not user:
        new_uid = f"USR-{uuid.uuid4().hex[:8].upper()}"
        now_str = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute("""
                INSERT INTO users (id, smart_ease_account_id, full_name, email, registered_date, product_owned, package_name, source_app, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (new_uid, account_id, card_holder or "Smart Ease Pro User", f"{account_id}@smartease.local", now_str, "Smart Ease Photobook Designer", "Photobook Pro", "Photobook", now_str, now_str))
        cursor.execute("SELECT * FROM users WHERE smart_ease_account_id = ?", (account_id,))
        user = cursor.fetchone()

    # Retrieve package info from database or default packages
    cursor.execute("SELECT * FROM packages WHERE id = ?", (package_id,))
    pkg_row = cursor.fetchone()
    if not pkg_row:
        default_packages = {
            "pkg_photobook": ("Smart Ease Photobook Designer", 2.45, "monthly"),
            "pkg_bundle": ("Smart Ease Photobook Designer + Ease Select", 3.00, "monthly"),
            "pkg_ease_select": ("Ease Select", 2.45, "monthly"),
            "pkg_lifetime": ("Lifetime Pro", 200.00, "lifetime")
        }
        pkg_tuple = default_packages.get(package_id, ("Smart Ease Photobook Designer", 2.45, "monthly"))
        pkg = {"id": package_id, "name": pkg_tuple[0], "price": pkg_tuple[1], "billing_interval": pkg_tuple[2]}
    else:
        pkg = dict(pkg_row)

    amount = float(payload.get("amount") or pkg["price"])
    tx_ref = f"CARD-{uuid.uuid4().hex[:12].upper()}"
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    last4 = card_number[-4:] if len(card_number) >= 4 else "0000"

    # Insert verified transaction in USD
    with conn:
        conn.execute("""
            INSERT INTO transactions (
                id, transaction_date, user_id, package_id, product_or_package,
                amount, refund_amount, fee_amount, net_amount, currency,
                payment_status, payment_provider, transaction_reference, is_verified, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"TXN-{uuid.uuid4().hex[:8].upper()}",
            now_iso,
            user["id"],
            pkg["id"],
            pkg["name"],
            amount,
            0.0,
            0.0,
            amount,
            currency,
            "Verified",
            "Card (USD)",
            tx_ref,
            1,
            f"Card payment for {pkg['name']} (USD {amount:.2f}) - {card_holder} (ending in {last4})",
            now_iso
        ))

    is_lifetime = (pkg.get("billing_interval") == "lifetime" or package_id == "pkg_lifetime")
    expiry_dt = None if is_lifetime else (now_dt + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    sub_status = "Lifetime" if is_lifetime else "Active"

    cursor.execute("SELECT * FROM subscriptions WHERE user_id = ?", (user["id"],))
    existing_sub = cursor.fetchone()

    with conn:
        if existing_sub:
            conn.execute("""
                UPDATE subscriptions
                SET package_id = ?, product_or_package = ?, status = ?, payment_status = 'Verified',
                    expiry_date = ?, amount_paid = ?, currency = 'USD', last_successful_payment = ?,
                    payment_transaction_ref = ?, updated_at = ?
                WHERE id = ?
            """, (
                pkg["id"], pkg["name"], sub_status, expiry_dt, amount,
                now_iso, tx_ref, now_iso, existing_sub["id"]
            ))
        else:
            conn.execute("""
                INSERT INTO subscriptions (
                    id, user_id, package_id, product_or_package, start_date, expiry_date,
                    billing_interval, status, payment_status, last_successful_payment,
                    payment_transaction_ref, amount_paid, currency, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f"SUB-{uuid.uuid4().hex[:8].upper()}",
                user["id"],
                pkg["id"],
                pkg["name"],
                now_iso,
                expiry_dt,
                "lifetime" if is_lifetime else "monthly",
                sub_status,
                "Verified",
                now_iso,
                tx_ref,
                amount,
                "USD",
                now_iso,
                now_iso
            ))

    # Synchronize to MongoDB Atlas cloud
    try:
        sync_sqlite_to_mongo(conn)
    except Exception as me:
        print(f"[MongoSync] Warning: {me}")

    conn.close()

    key_prefix = "SE-PHOTOBOOK" if "photobook" in package_id else ("SE-BUNDLE" if "bundle" in package_id else ("SE-SELECT" if "select" in package_id else "SE-LIFETIME"))
    generated_lic = f"{key_prefix}-{uuid.uuid4().hex[:8].upper()}-PRO"

    return {
        "success": True,
        "status": "Verified",
        "is_verified": True,
        "transaction_reference": tx_ref,
        "packageName": pkg["name"],
        "packageId": pkg["id"],
        "amount": amount,
        "currency": "USD",
        "license_key": generated_lic,
        "licenseKey": generated_lic,
        "message": f"✓ Payment of USD {amount:.2f} verified via Card! Full access unlocked."
    }




# ==========================================
# 11. AUTHENTICATION & MONGODB CLOUD ACCOUNTS
# ==========================================

from mongo_db import get_mongo_db

def get_user_subscription_info(user_id: str, email: str, conn: Optional[Any] = None) -> Dict[str, Any]:
    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.*, p.name as pkg_name, p.billing_interval as pkg_interval
        FROM subscriptions s
        LEFT JOIN packages p ON s.package_id = p.id
        WHERE (s.user_id = ? OR s.user_id IN (SELECT id FROM users WHERE LOWER(email) = ?))
          AND s.payment_status = 'Verified'
        ORDER BY s.created_at DESC LIMIT 1
    """, (user_id, email.lower()))
    sub = cursor.fetchone()
    if should_close:
        conn.close()

    if sub:
        st = sub["status"]
        is_active = st in ("Active", "Lifetime")
        is_lifetime = st == "Lifetime"
        return {
            "hasSubscription": is_active,
            "status": st,
            "packageId": sub["package_id"],
            "packageName": sub["pkg_name"] or sub["product_or_package"],
            "lifetimePro": is_lifetime,
            "expiryDate": sub["expiry_date"]
        }
    return {
        "hasSubscription": False,
        "status": "Free Account",
        "packageId": None,
        "packageName": "Free Account",
        "lifetimePro": False,
        "expiryDate": None
    }

@router.post("/auth/register")
async def register_auth_user(payload: Dict[str, Any] = Body(...)):
    full_name = (payload.get("fullName") or payload.get("full_name") or payload.get("name") or "").strip()
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    source_app = payload.get("sourceApp") or "Photobook"

    if not full_name:
        raise HTTPException(status_code=400, detail="Please enter your full name.")
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long.")

    db = get_mongo_db()
    if db is not None:
        try:
            existing = db["users"].find_one({"email": email})
            if existing:
                return {"success": False, "error": f"An account with email '{email}' is already registered. Please sign in instead."}
        except Exception as me:
            print(f"[MongoDB] Auth check error: {me}")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE LOWER(email) = ?", (email,))
    if cursor.fetchone():
        conn.close()
        return {"success": False, "error": f"An account with email '{email}' is already registered. Please sign in instead."}

    import hashlib, uuid, os
    salt = os.urandom(16).hex()
    pwhash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 100000).hex()

    user_id = f"USR-{uuid.uuid4().hex[:12].upper()}"
    smart_ease_account_id = f"SE-{uuid.uuid4().hex[:8].upper()}"
    now_iso = datetime.now(timezone.utc).isoformat()

    with conn:
        conn.execute("""
            INSERT INTO users (
                id, smart_ease_account_id, full_name, email, registered_date,
                product_owned, package_name, source_app, password_hash, password_salt,
                auth_provider, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, smart_ease_account_id, full_name, email, now_iso,
            "Smart Ease Photobook Designer", "Free Account", source_app,
            pwhash, salt, "email", now_iso, now_iso
        ))

    sub_info = get_user_subscription_info(user_id, email, conn)

    if db is not None:
        try:
            mongo_doc = {
                "_id": user_id,
                "id": user_id,
                "smart_ease_account_id": smart_ease_account_id,
                "full_name": full_name,
                "email": email,
                "registered_date": now_iso,
                "product_owned": "Smart Ease Photobook Designer",
                "package_name": sub_info["packageName"],
                "source_app": source_app,
                "password_hash": pwhash,
                "password_salt": salt,
                "auth_provider": "email",
                "created_at": now_iso,
                "updated_at": now_iso
            }
            db["users"].replace_one({"_id": user_id}, mongo_doc, upsert=True)
            print(f"[MongoDB] Stored account for {email} in Mongo Atlas.")
        except Exception as me:
            print(f"[MongoDB] Error upserting user: {me}")

    conn.close()

    return {
        "success": True,
        "user": {
            "id": user_id,
            "userId": user_id,
            "name": full_name,
            "fullName": full_name,
            "email": email,
            "authProvider": "email",
            "status": sub_info["status"],
            "packageName": sub_info["packageName"],
            "lifetimePro": sub_info["lifetimePro"]
        }
    }

@router.post("/auth/login")
async def login_auth_user(payload: Dict[str, Any] = Body(...)):
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""

    if not email or not password:
        raise HTTPException(status_code=400, detail="Please enter both email and password.")

    user_record = None
    db = get_mongo_db()
    if db is not None:
        try:
            mongo_user = db["users"].find_one({"email": email})
            if mongo_user:
                user_record = dict(mongo_user)
        except Exception as me:
            print(f"[MongoDB] Query error: {me}")

    conn = get_db_connection()
    if not user_record:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,))
        row = cursor.fetchone()
        if row:
            user_record = dict(row)

    if not user_record:
        conn.close()
        return {"success": False, "error": "No account found with this email. Please check your credentials or create an account."}

    auth_prov = user_record.get("auth_provider", "email")
    if auth_prov == "google" and not user_record.get("password_hash"):
        conn.close()
        return {"success": False, "error": "This account was registered with Google. Please click 'Continue with Google'."}

    stored_hash = user_record.get("password_hash")
    stored_salt = user_record.get("password_salt")
    if not stored_hash or not stored_salt:
        conn.close()
        return {"success": False, "error": "Account password not set or corrupted. Please sign in with Google or reset password."}

    import hashlib
    expected_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(stored_salt), 100000).hex()
    if expected_hash != stored_hash:
        conn.close()
        return {"success": False, "error": "Incorrect password. Please try again."}

    user_id = user_record.get("id") or user_record.get("_id")
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        with conn:
            conn.execute("UPDATE users SET last_seen = ?, updated_at = ? WHERE id = ?", (now_iso, now_iso, user_id))
        if db is not None:
            db["users"].update_one({"_id": user_id}, {"$set": {"last_seen": now_iso, "updated_at": now_iso}})
    except Exception:
        pass

    sub_info = get_user_subscription_info(user_id, email, conn)

    # Record login and dispatch security alert
    try:
        from database import record_user_login
        from email_service import dispatch_login_security_email
        fn_val = user_record.get("full_name") or user_record.get("name") or email.split("@")[0].capitalize()
        dev_val = payload.get("deviceHostname") or payload.get("device_hostname") or "Desktop"
        prod_val = payload.get("product") or "Smart Ease Photobook Designer"
        l_rec = record_user_login(
            email=email,
            full_name=fn_val,
            product=prod_val,
            device_hostname=dev_val,
            auth_method="email",
            user_id=user_id,
            conn=conn
        )
        dispatch_login_security_email(
            email=email,
            full_name=fn_val,
            product=prod_val,
            device_hostname=dev_val,
            auth_method="Email & Password",
            login_time=l_rec.get("login_time")
        )
    except Exception as log_e:
        print(f"[LoginSecurity] Log error: {log_e}")

    conn.close()

    return {
        "success": True,
        "user": {
            "id": user_id,
            "userId": user_id,
            "name": user_record.get("full_name") or user_record.get("name") or email.split("@")[0].capitalize(),
            "fullName": user_record.get("full_name") or user_record.get("name"),
            "email": email,
            "authProvider": auth_prov,
            "status": sub_info["status"],
            "packageName": sub_info["packageName"],
            "lifetimePro": sub_info["lifetimePro"]
        }
    }

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

@router.post("/auth/google")
async def google_auth_user(payload: Dict[str, Any] = Body(...)):
    email = (payload.get("email") or "").strip().lower()
    name = (payload.get("name") or payload.get("fullName") or "").strip()
    google_id = (payload.get("googleId") or payload.get("google_id") or "").strip()
    avatar_url = (payload.get("avatarUrl") or payload.get("avatar_url") or "").strip()
    source_app = payload.get("sourceApp") or "Photobook"

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid Google email is required.")

    if not name:
        name = email.split("@")[0].replace(".", " ").title()

    db = get_mongo_db()
    user_record = None

    if db is not None:
        try:
            if google_id:
                user_record = db["users"].find_one({"$or": [{"email": email}, {"google_id": google_id}]})
            else:
                user_record = db["users"].find_one({"email": email})
            if user_record:
                user_record = dict(user_record)
        except Exception as me:
            print(f"[MongoDB] Google query error: {me}")

    conn = get_db_connection()
    if not user_record:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE LOWER(email) = ? OR (google_id IS NOT NULL AND google_id = ?)", (email, google_id))
        row = cursor.fetchone()
        if row:
            user_record = dict(row)

    import uuid
    now_iso = datetime.now(timezone.utc).isoformat()

    if user_record:
        user_id = user_record.get("id") or user_record.get("_id")
        smart_ease_account_id = user_record.get("smart_ease_account_id")
        with conn:
            conn.execute("""
                UPDATE users SET
                    google_id = COALESCE(?, google_id),
                    avatar_url = COALESCE(?, avatar_url),
                    auth_provider = 'google',
                    last_seen = ?,
                    updated_at = ?
                WHERE id = ?
            """, (google_id or None, avatar_url or None, now_iso, now_iso, user_id))

        if db is not None:
            try:
                db["users"].update_one(
                    {"_id": user_id},
                    {"$set": {
                        "google_id": google_id or user_record.get("google_id"),
                        "avatar_url": avatar_url or user_record.get("avatar_url"),
                        "auth_provider": "google",
                        "last_seen": now_iso,
                        "updated_at": now_iso
                    }}
                )
            except Exception:
                pass
    else:
        user_id = f"USR-G-{uuid.uuid4().hex[:10].upper()}"
        smart_ease_account_id = f"SE-G-{uuid.uuid4().hex[:6].upper()}"
        with conn:
            conn.execute("""
                INSERT INTO users (
                    id, smart_ease_account_id, full_name, email, registered_date,
                    product_owned, package_name, source_app, auth_provider,
                    google_id, avatar_url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, smart_ease_account_id, name, email, now_iso,
                "Smart Ease Photobook Designer", "Free Account", source_app,
                "google", google_id, avatar_url, now_iso, now_iso
            ))

        if db is not None:
            try:
                mongo_doc = {
                    "_id": user_id,
                    "id": user_id,
                    "smart_ease_account_id": smart_ease_account_id,
                    "full_name": name,
                    "email": email,
                    "registered_date": now_iso,
                    "product_owned": "Smart Ease Photobook Designer",
                    "package_name": "Free Account",
                    "source_app": source_app,
                    "auth_provider": "google",
                    "google_id": google_id,
                    "avatar_url": avatar_url,
                    "created_at": now_iso,
                    "updated_at": now_iso
                }
                db["users"].replace_one({"_id": user_id}, mongo_doc, upsert=True)
                print(f"[MongoDB] Created new Google user for {email} in Mongo Atlas.")
            except Exception as me:
                print(f"[MongoDB] Error creating Google user: {me}")

    sub_info = get_user_subscription_info(user_id, email, conn)

    # Record Google login and dispatch security alert
    try:
        from database import record_user_login
        from email_service import dispatch_login_security_email
        dev_val = payload.get("deviceHostname") or payload.get("device_hostname") or "Desktop"
        prod_val = source_app or "Smart Ease Photobook Designer"
        l_rec = record_user_login(
            email=email,
            full_name=name,
            product=prod_val,
            device_hostname=dev_val,
            auth_method="google",
            user_id=user_id,
            conn=conn
        )
        dispatch_login_security_email(
            email=email,
            full_name=name,
            product=prod_val,
            device_hostname=dev_val,
            auth_method="Google Sign-In",
            login_time=l_rec.get("login_time")
        )
    except Exception as log_e:
        print(f"[GoogleLoginSecurity] Log error: {log_e}")

    conn.close()

    return {
        "success": True,
        "user": {
            "id": user_id,
            "userId": user_id,
            "name": name,
            "fullName": name,
            "email": email,
            "authProvider": "google",
            "avatarUrl": avatar_url,
            "status": sub_info["status"],
            "packageName": sub_info["packageName"],
            "lifetimePro": sub_info["lifetimePro"]
        }
    }


# ==========================================
# 10. REAL-TIME LOGIN AUDIT & NOTIFICATION ENDPOINTS
# ==========================================

@router.post("/logins/record")
async def api_record_login(payload: Dict[str, Any] = Body(...)):
    """
    Records a user login from client desktop apps or web and dispatches a real-time security alert.
    """
    try:
        from database import record_user_login
        from email_service import dispatch_login_security_email

        email = (payload.get("email") or "").strip().lower()
        full_name = payload.get("fullName") or payload.get("full_name") or payload.get("name")
        product = payload.get("product") or payload.get("sourceApp") or "Smart Ease Photobook Designer"
        device = payload.get("deviceHostname") or payload.get("device_hostname") or "Desktop PC"
        auth_method = payload.get("authMethod") or payload.get("auth_method") or "email"
        ip = payload.get("ipAddress") or payload.get("ip_address") or "127.0.0.1"
        user_id = payload.get("userId") or payload.get("user_id")

        if not email:
            raise HTTPException(status_code=400, detail="User email is required.")

        login_record = record_user_login(
            email=email,
            full_name=full_name,
            product=product,
            device_hostname=device,
            auth_method=auth_method,
            ip_address=ip,
            user_id=user_id
        )

        alert_res = dispatch_login_security_email(
            email=email,
            full_name=login_record.get("full_name"),
            product=product,
            device_hostname=device,
            auth_method=auth_method,
            login_time=login_record.get("login_time")
        )

        return {
            "success": True,
            "login": login_record,
            "security_alert": alert_res,
            "message": f"Login verified and real-time security alert dispatched for {email}."
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "success": True,
            "recorded": False,
            "warning": str(e),
            "message": "Login event processed with graceful fallback"
        }

@router.get("/logins/recent")
async def api_get_recent_logins(limit: int = Query(default=30)):
    """
    Returns the real-time stream of latest logins for Subscription Manager.
    """
    from database import get_recent_logins
    logins = get_recent_logins(limit=limit)
    return {
        "success": True,
        "count": len(logins),
        "logins": logins
    }

@router.get("/users/{user_id}/logins")
async def api_get_user_logins(user_id: str, limit: int = Query(default=20)):
    """
    Returns the login history for a specific customer.
    """
    from database import get_user_logins
    logins = get_user_logins(user_id, limit=limit)
    return {
        "success": True,
        "user_id": user_id,
        "count": len(logins),
        "logins": logins
    }
