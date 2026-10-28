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
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
import httpx
from fastapi import APIRouter, HTTPException, Query, Body, Response, Depends, Request, Header
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
from security import (
    require_user, require_admin, create_session, delete_session, delete_user_sessions,
    verify_livepay_signature, livepay_create_card_checkout, livepay_transaction_status,
)

router = APIRouter(prefix="/api/v1")

# Every admin-console endpoint takes this dependency; customer endpoints use require_user.
ADMIN = [Depends(require_admin)]

# Card payment choices per package (LivePay cards need at least CARD_MIN_USD per payment).
# Each month paid gives 30 days of access. Packages without options (old Photobook + Ease Select
# bundle, Lifetime) are no longer sold; existing holders keep their access.
PLAN_OPTIONS = {
    "pkg_all_in_one": [(1, 9.13), (6, 45.65), (12, 91.27)],
    "pkg_photobook": [(1, 2.28), (6, 11.41), (12, 22.82)],
    "pkg_ease_select": [(1, 2.28), (6, 11.41), (12, 22.82)],
    "pkg_retouch_academy": [(1, 6.85), (6, 34.23), (12, 61.61)],
}
PLAN_SORT_ORDER = {"pkg_all_in_one": 0, "pkg_photobook": 1, "pkg_ease_select": 2, "pkg_retouch_academy": 3}
DAYS_PER_MONTH = 30
CARD_MIN_USD = float(os.environ.get("LIVEPAY_CARD_MIN_USD") or 1)

# Ease Creative Suite apps and which package access values unlock them.
PRODUCTS = ("photobook", "ease_select", "retouch_academy")
ACCESS_COVERS = {
    "all": set(PRODUCTS),
    "both": {"photobook", "ease_select"},  # old bundle / lifetime holders
    "photobook": {"photobook"},
    "ease_select": {"ease_select"},
    "retouch_academy": {"retouch_academy"},
}


def normalize_product(product: Optional[str]) -> str:
    p = (product or "photobook").strip().lower().replace("-", "_").replace(" ", "_")
    if p in ("select", "ease_select", "easeselect"):
        return "ease_select"
    if p in ("retouch", "retouching", "retouch_academy", "retouching_academy", "ease_retouching_academy", "academy"):
        return "retouch_academy"
    return "photobook"


def access_includes(product_access: Optional[str], product: str) -> bool:
    return product in ACCESS_COVERS.get((product_access or "").lower(), set())


def plan_options(pkg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Choices customers can pay for. Packages without options aren't sold;
    admins can still grant them from the console, and existing holders keep their access."""
    def label(m):
        return "1 month" if m == 1 else ("1 year" if m == 12 else f"{m} months")
    return [{"months": m, "price": p, "label": label(m)} for m, p in PLAN_OPTIONS.get(pkg["id"], [])]

# Helper to format currency
def fmt_money(val: float) -> float:
    return round(float(val or 0.0), 2)

# ==========================================
# 1. MAIN DASHBOARD ENDPOINTS
# ==========================================

@router.get("/dashboard/metrics", dependencies=ADMIN)
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

@router.get("/dashboard/chart", dependencies=ADMIN)
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

@router.get("/users", dependencies=ADMIN)
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
        has_all_in_one = any(s["package_id"] == "pkg_all_in_one" for s in active_subs)
        has_retouch = any(s["package_id"] == "pkg_retouch_academy" for s in active_subs)

        if has_all_in_one:
            u["app_access_code"] = "all_in_one"
            u["app_access_label"] = "All Apps (All-in-One)"
            u["app_access_category"] = "both"
            u["app_access_badge"] = "badge-indigo"
            u["product_owned"] = "All Ease Creative Suite apps"
        elif has_retouch and not (has_lifetime or has_bundle or has_ind_pb or has_ind_es):
            u["app_access_code"] = "independent_retouch_academy"
            u["app_access_label"] = "Retouching Academy (Independent Sub)"
            u["app_access_category"] = "independent"
            u["app_access_badge"] = "badge-purple"
            u["product_owned"] = "Ease Retouching Academy"
        elif has_lifetime:
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
                elif pkg == "pkg_retouch_academy":
                    u["app_access_code"] = "independent_retouch_academy"
                    u["app_access_label"] = "Retouching Academy (Expired)"
                    u["app_access_category"] = "independent"
                    u["app_access_badge"] = "badge-danger"
                elif pkg == "pkg_all_in_one":
                    u["app_access_code"] = "all_in_one"
                    u["app_access_label"] = "All Apps (Expired)"
                    u["app_access_category"] = "both"
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

        # Retouching Academy bought alongside Photobook / Ease Select subscriptions
        if has_retouch and u.get("app_access_code") not in ("all_in_one", "independent_retouch_academy"):
            u["app_access_label"] = f'{u["app_access_label"]} + Retouching Academy'

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

@router.get("/users/{user_id}", dependencies=ADMIN)
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

@router.post("/users", dependencies=ADMIN)
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
async def get_packages(authorization: Optional[str] = Header(default=None)):
    # Public (the apps show plans); sales figures only for admins.
    try:
        await require_admin(authorization)
        is_admin = True
    except HTTPException:
        is_admin = False

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM packages ORDER BY price ASC")
    packages = [dict(p) for p in cursor.fetchall()]

    for pkg in packages:
        pkg_id = pkg["id"]
        pkg["options"] = plan_options(pkg)
        pkg["for_sale"] = bool(pkg["options"]) and bool(pkg.get("is_active", 1))
        pkg["sort_order"] = PLAN_SORT_ORDER.get(pkg_id, 99)
        if not is_admin:
            continue
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

@router.put("/packages/{package_id}", dependencies=ADMIN)
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

@router.get("/subscriptions", dependencies=ADMIN)
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

@router.post("/subscriptions", dependencies=ADMIN)
async def create_or_extend_subscription(payload: Dict[str, Any] = Body(...)):
    """
    Admin console: turn a customer's access on.
    - payment_provider "Admin Grant": free access for `days` days (not counted as revenue).
    - any other provider: records a payment the admin received outside LivePay.
    `days` sets how long access lasts; omit it only for lifetime packages.
    """
    user_id = payload.get("user_id")
    package_id = payload.get("package_id")
    if not user_id or not package_id:
        raise HTTPException(status_code=400, detail="user_id and package_id are required")

    provider = (payload.get("payment_provider") or "Admin Grant").strip()
    is_grant = provider.lower() == "admin grant"
    days_raw = payload.get("days")
    days = None
    if days_raw not in (None, ""):
        try:
            days = int(days_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="days must be a whole number")
        if days < 1 or days > 3650:
            raise HTTPException(status_code=400, detail="days must be between 1 and 3650")

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Customer not found")
        cursor.execute("SELECT * FROM packages WHERE id = ?", (package_id,))
        pkg = cursor.fetchone()
        if not pkg:
            raise HTTPException(status_code=404, detail="Package not found")
        pkg = dict(pkg)
        is_lifetime = pkg["billing_type"] == "one_time" or pkg["billing_interval"] == "lifetime"
        if days is None and not is_lifetime:
            raise HTTPException(status_code=400, detail="Please enter how many days of access to give.")

        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        amount_paid = 0.0 if is_grant else float(payload.get("amount", pkg["price"]))
        prefix = "ADMIN" if is_grant else "MANUAL"
        tx_ref = f"{prefix}-{uuid.uuid4().hex[:10].upper()}"
        note = payload.get("notes") or (f"Admin granted {days} days" if (is_grant and days) else "Admin recorded payment")
        if not is_grant and payload.get("transaction_reference"):
            note = f"{note} (ref {payload.get('transaction_reference')})"

        txn_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"
        with conn:
            conn.execute("""
                INSERT INTO transactions (
                    id, transaction_date, user_id, package_id,
                    product_or_package, amount, refund_amount, fee_amount, net_amount,
                    currency, payment_status, payment_provider, transaction_reference,
                    is_verified, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0.0, 0.0, ?, 'USD', 'Verified', ?, ?, 1, ?, ?)
            """, (txn_id, now_str, user_id, package_id, pkg["name"], amount_paid, amount_paid,
                  provider, tx_ref, note, now_str))
        sub_id = grant_subscription(conn, user_id, pkg, days, tx_ref, amount_paid, provider, note)
        recalculate_all_subscription_statuses(conn)  # also syncs to MongoDB
        return {"status": "success", "subscription_id": sub_id, "transaction_id": txn_id}
    finally:
        conn.close()

@router.post("/subscriptions/{sub_id}/cancel", dependencies=ADMIN)
async def cancel_subscription(sub_id: str):
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    with conn:
        conn.execute("""
            UPDATE subscriptions
            SET cancellation_date = ?, status = 'Cancelled', updated_at = ?
            WHERE id = ?
        """, (now_str, now_str, sub_id))
    recalculate_all_subscription_statuses(conn)  # also saves to MongoDB
    conn.close()
    return {"status": "success", "message": "Subscription cancelled"}


@router.post("/users/{user_id}/end-pass", dependencies=ADMIN)
async def end_user_pass(user_id: str):
    """Admin: stop all of a customer's current access now (paid or granted).
    The customer's app locks the next time it checks in online."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    try:
        if not conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Customer not found")
        with conn:
            cur = conn.execute("""
                UPDATE subscriptions
                SET cancellation_date = ?, status = 'Cancelled', updated_at = ?
                WHERE user_id = ? AND payment_status = 'Verified'
                  AND status IN ('Active', 'Expiring Soon', 'Lifetime')
            """, (now_str, now_str, user_id))
            ended = cur.rowcount
            conn.execute("UPDATE users SET package_name = 'No active license', updated_at = ? WHERE id = ?", (now_str, user_id))
        recalculate_all_subscription_statuses(conn)  # also saves to MongoDB
        return {"status": "success", "ended": ended}
    finally:
        conn.close()

# ==========================================
# 5. DAILY AND MONTHLY EARNINGS ENDPOINTS
# ==========================================

@router.get("/revenue/daily", dependencies=ADMIN)
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

@router.get("/revenue/monthly", dependencies=ADMIN)
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

@router.get("/transactions", dependencies=ADMIN)
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

@router.post("/transactions", dependencies=ADMIN)
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

@router.get("/apps/status", dependencies=ADMIN)
async def get_apps_status():
    status = get_connected_apps_status()
    return status

@router.post("/apps/sync-local", dependencies=ADMIN)
async def trigger_local_sync():
    conn = get_db_connection()
    res = sync_local_app_identities(conn)
    conn.close()
    return res

@router.get("/apps/render/ping", dependencies=ADMIN)
@router.post("/apps/render/ping", dependencies=ADMIN)
async def api_ping_render():
    """Real-time ping to the Render production cloud server & MongoDB Atlas."""
    return ping_render_server()

@router.post("/apps/render/sync", dependencies=ADMIN)
async def api_sync_render():
    """Synchronizes users and subscriptions from Render Cloud MongoDB into local database."""
    conn = get_db_connection()
    res = sync_render_cloud_data(conn)
    recalculate_all_subscription_statuses(conn)
    conn.close()
    return res

@router.get("/apps/photobook/ping", dependencies=ADMIN)
@router.post("/apps/photobook/ping", dependencies=ADMIN)
async def api_ping_photobook():
    """Real-time live ping to the Photobook Studio server on port 8765."""
    return ping_photobook_server()

@router.post("/apps/photobook/push-license", dependencies=ADMIN)
async def api_push_photobook_license(payload: Optional[Dict[str, Any]] = Body(default={})):
    """Pushes verified license to local Photobook instance so all buttons work."""
    key = (payload or {}).get("licenseKey") or "SE-LIFETIME-PRO"
    email = (payload or {}).get("email") or "studio@rafiapictures.com"
    return push_license_to_photobook(license_key=key, email=email)

@router.post("/apps/photobook/launch", dependencies=ADMIN)
async def api_launch_photobook():
    """Launches Photobook Studio launcher in background if not running."""
    import subprocess
    bat_path = r"D:\Antigravity\photobook\version zero\Launch-In-Chrome.bat"
    if os.path.exists(bat_path):
        subprocess.Popen(["cmd.exe", "/c", bat_path], cwd=os.path.dirname(bat_path))
        return {"status": "success", "message": "Photobook launcher invoked"}
    return {"status": "error", "message": "Launcher bat file not found"}

@router.post("/client/verify-license")
async def client_verify_license(payload: Dict[str, Any] = Body(default={}), user: Dict[str, Any] = Depends(require_user)):
    """
    Called by Photobook Designer or Ease Select (signed in) to check the customer's license.
    The account comes from the session token, never from the request body.
    """
    product = (payload.get("product") or "photobook").lower()
    product = normalize_product(product)
    lic = get_license_status(user["id"], user.get("email") or "", product=product)

    try:
        record_presence_heartbeat(
            account_id=user["smart_ease_account_id"],
            product=product,
            mode=payload.get("mode") or "online",
            device_hostname=payload.get("device_hostname"),
            ip_address=payload.get("ip") or "127.0.0.1"
        )
    except Exception as e:
        print(f"[Presence] Heartbeat record warning: {e}")

    return {
        "has_access": lic["active"],
        "status": lic["status"],
        "customer_name": user.get("full_name"),
        "account_id": user["smart_ease_account_id"],
        "package_name": lic["packageName"],
        "is_lifetime": lic["lifetime"],
        "expiry_date": lic["expiryDate"],
        "license": lic,
        "message": f"License verified: {lic['status']}" if lic["active"] else "No active license for this product"
    }

@router.post("/client/heartbeat")
async def client_heartbeat(payload: Dict[str, Any] = Body(default={}), user: Dict[str, Any] = Depends(require_user)):
    """
    Heartbeat endpoint for client applications (Photobook Designer, Ease Select).
    Updates presence in real-time so administrator knows who is Online vs Offline.
    """
    account_id = user["smart_ease_account_id"]

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

@router.get("/presence/metrics", dependencies=ADMIN)
async def get_presence_stats():
    """Returns dynamic counts of online, offline, and offline-mode users."""
    return get_presence_metrics()

@router.post("/users/{user_id}/toggle-mode", dependencies=ADMIN)
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

@router.get("/export/users.csv", dependencies=ADMIN)
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
# 8. LIVEPAY CARD PAYMENTS (USD)
# ==========================================
# Flow: the signed-in app calls /payments/card/checkout -> LivePay hosted checkout page ->
# LivePay webhook (signature-checked) or the app's status poll (re-checked with LivePay's API)
# -> activate_paid_license(). Prices always come from the server, never from the client.

import os
import uuid
from mongo_db import sync_sqlite_to_mongo

LIVEPAY_WEBHOOK_URL = os.environ.get(
    "LIVEPAY_WEBHOOK_URL",
    "https://smart-ease-photobook-designer.onrender.com/api/v1/payments/livepay/webhook",
)
PAYMENT_RETURN_URL = os.environ.get("PAYMENT_RETURN_URL", "")
LIVEPAY_SUCCESS_STATUSES = ("success", "successful", "completed")
LIVEPAY_FAILED_STATUSES = ("failed", "failure", "cancelled", "canceled", "declined", "expired", "rejected")

PACKAGE_ALIASES = {"pkg_select": "pkg_ease_select"}


def _get_active_package(cursor, package_id: str) -> Dict[str, Any]:
    package_id = PACKAGE_ALIASES.get(package_id, package_id)
    cursor.execute("SELECT * FROM packages WHERE id = ? AND is_active = 1", (package_id,))
    pkg = cursor.fetchone()
    if not pkg:
        raise HTTPException(status_code=404, detail="Unknown or inactive package.")
    return dict(pkg)


def _new_expiry(cursor, user_id: str, package_id: str, days: int) -> str:
    """Extends from the end of a still-running subscription for the same package, else from now."""
    start = datetime.now(timezone.utc).replace(tzinfo=None)
    cursor.execute("""
        SELECT expiry_date FROM subscriptions
        WHERE user_id = ? AND package_id = ? AND payment_status = 'Verified'
          AND status IN ('Active', 'Expiring Soon') AND expiry_date IS NOT NULL
        ORDER BY expiry_date DESC LIMIT 1
    """, (user_id, package_id))
    row = cursor.fetchone()
    if row:
        try:
            current = datetime.strptime(row["expiry_date"].split(".")[0].replace("T", " "), "%Y-%m-%d %H:%M:%S")
            if current > start:
                start = current
        except ValueError:
            pass
    return (start + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def grant_subscription(conn, user_id: str, pkg: Dict[str, Any], days: Optional[int],
                       tx_ref: str, amount: float, provider: str, notes: str) -> str:
    """Creates a verified subscription. days=None means lifetime (only for lifetime packages)."""
    cursor = conn.cursor()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    is_lifetime = pkg.get("billing_interval") == "lifetime" or pkg.get("billing_type") == "one_time"
    if days is None and not is_lifetime:
        days = 30
    expiry = None if days is None else _new_expiry(cursor, user_id, pkg["id"], days)
    sub_id = f"SUB-{uuid.uuid4().hex[:10].upper()}"
    with conn:
        conn.execute("""
            INSERT INTO subscriptions (
                id, user_id, package_id, product_or_package, start_date, expiry_date,
                billing_interval, status, payment_status, next_renewal_date,
                last_successful_payment, payment_transaction_ref, amount_paid, currency,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Verified', ?, ?, ?, ?, 'USD', ?, ?)
        """, (
            sub_id, user_id, pkg["id"], pkg["name"], now_str, expiry,
            "lifetime" if expiry is None else pkg.get("billing_interval", "monthly"),
            "Lifetime" if expiry is None else "Active",
            None if expiry is None else expiry[:10],
            now_str, tx_ref, amount, now_str, now_str,
        ))
        conn.execute("UPDATE users SET package_name = ?, updated_at = ? WHERE id = ?", (pkg["name"], now_str, user_id))
        conn.execute("UPDATE transactions SET subscription_id = ? WHERE transaction_reference = ?", (sub_id, tx_ref))
    print(f"[License] {provider}: {pkg['id']} for {user_id} until {expiry or 'lifetime'} ({notes})")
    return sub_id


def activate_paid_license(conn, tx_ref: str, paid_amount: Optional[float], paid_currency: Optional[str], source: str) -> bool:
    """Marks a pending LivePay transaction verified and grants its package. Safe to call twice."""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM transactions WHERE transaction_reference = ?", (tx_ref,))
    txn = cursor.fetchone()
    if not txn:
        print(f"[LivePay] {source}: unknown reference {tx_ref}")
        return False
    if txn["is_verified"]:
        return True  # already processed
    if paid_amount is not None and float(paid_amount) + 0.001 < float(txn["amount"]):
        print(f"[LivePay] {source}: amount mismatch for {tx_ref} (paid {paid_amount}, expected {txn['amount']})")
        return False
    if paid_currency and paid_currency.upper() != (txn["currency"] or "USD").upper():
        print(f"[LivePay] {source}: currency mismatch for {tx_ref}")
        return False

    cursor.execute("SELECT * FROM packages WHERE id = ?", (txn["package_id"],))
    pkg_row = cursor.fetchone()
    if not pkg_row:
        print(f"[LivePay] {source}: package {txn['package_id']} missing for {tx_ref}")
        return False
    with conn:
        conn.execute("""
            UPDATE transactions SET payment_status = 'Verified', is_verified = 1, notes = ?
            WHERE transaction_reference = ? AND is_verified = 0
        """, (f"Verified by LivePay ({source})", tx_ref))
    paid_days = txn["access_days"] if "access_days" in txn.keys() else None
    grant_subscription(conn, txn["user_id"], dict(pkg_row), paid_days, tx_ref, float(txn["amount"]), "LivePay Card", source)
    recalculate_all_subscription_statuses(conn)  # also syncs to MongoDB
    return True


def _mark_failed(conn, tx_ref: str, reason: str):
    with conn:
        conn.execute("""
            UPDATE transactions SET payment_status = 'Failed', notes = ?
            WHERE transaction_reference = ? AND is_verified = 0
        """, (reason, tx_ref))
    sync_sqlite_to_mongo(conn)


@router.post("/payments/card/checkout")
async def card_checkout(payload: Dict[str, Any] = Body(...), user: Dict[str, Any] = Depends(require_user)):
    """Starts a LivePay hosted card checkout for the signed-in customer. Returns the checkout URL."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        pkg = _get_active_package(cursor, payload.get("package_id") or payload.get("packageId") or "")
        options = plan_options(pkg)
        if not options:
            raise HTTPException(status_code=400, detail="This plan is not available for purchase.")
        try:
            months = int(payload.get("months") or 0)
        except (TypeError, ValueError):
            months = 0
        option = next((o for o in options if o["months"] == months), None)
        if not option:
            choices = ", ".join(str(o["months"]) for o in options)
            raise HTTPException(status_code=400, detail=f"Please choose how many months to pay for ({choices}).")
        amount = fmt_money(option["price"])
        if amount < CARD_MIN_USD:
            raise HTTPException(status_code=400, detail=f"Card payments must be at least USD {CARD_MIN_USD:.2f}.")
        access_days = option["months"] * DAYS_PER_MONTH
        email = user.get("email") or ""
        if not email:
            raise HTTPException(status_code=400, detail="Your account needs an email address before paying.")
        if email_verification_required() and not _is_email_verified(conn, user["id"]):
            raise HTTPException(status_code=403, detail="Please confirm your email address before paying.")

        tx_ref = f"SE{uuid.uuid4().hex[:16].upper()}"
        now_iso = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute("""
                INSERT INTO transactions (
                    id, transaction_date, user_id, package_id, product_or_package,
                    amount, refund_amount, fee_amount, net_amount, currency,
                    payment_status, payment_provider, transaction_reference, is_verified, notes, created_at,
                    access_days
                ) VALUES (?, ?, ?, ?, ?, ?, 0.0, 0.0, ?, 'USD', 'Pending', 'LivePay Card', ?, 0, ?, ?, ?)
            """, (
                f"TXN-{uuid.uuid4().hex[:8].upper()}", now_iso, user["id"], pkg["id"], pkg["name"],
                amount, amount, tx_ref, f"Card checkout started ({option['label']})", now_iso, access_days,
            ))
        sync_sqlite_to_mongo(conn)

        try:
            checkout = await livepay_create_card_checkout(
                reference=tx_ref, amount=amount, email=email,
                description=f"Smart Ease: {pkg['name']} ({option['label']})",
                return_url=PAYMENT_RETURN_URL or None,
            )
        except HTTPException as e:
            _mark_failed(conn, tx_ref, f"Checkout could not be created: {e.detail}")
            raise

        return {
            "success": True,
            "reference": tx_ref,
            "checkout_url": checkout["checkout_url"],
            "packageId": pkg["id"],
            "packageName": pkg["name"],
            "months": option["months"],
            "amount": amount,
            "currency": "USD",
        }
    finally:
        conn.close()


@router.post("/payments/livepay/webhook")
async def livepay_webhook(request: Request):
    """LivePay calls this when a transaction completes. Only signature-verified calls are trusted."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not verify_livepay_signature(request.headers.get("x-webhook-signature"), LIVEPAY_WEBHOOK_URL, payload):
        print(f"[LivePay] Rejected webhook with missing/invalid signature for {payload.get('customer_reference')}")
        raise HTTPException(status_code=401, detail="Invalid signature")

    tx_ref = payload.get("customer_reference") or payload.get("reference")
    status = (payload.get("status") or "").lower()
    if not tx_ref:
        return {"status": "ignored", "reason": "No reference"}

    conn = get_db_connection()
    try:
        if status in LIVEPAY_SUCCESS_STATUSES:
            amount = payload.get("amount")
            activate_paid_license(conn, tx_ref, float(amount) if amount not in (None, "") else None,
                                  payload.get("currency"), "webhook")
        elif status in LIVEPAY_FAILED_STATUSES:
            _mark_failed(conn, tx_ref, f"LivePay reported: {payload.get('message') or status}")
    finally:
        conn.close()
    return {"status": "ok"}


@router.get("/payments/livepay/status/{reference}")
async def livepay_check_status(reference: str, user: Dict[str, Any] = Depends(require_user)):
    """Polled by the app after opening checkout. Confirms with LivePay directly if still pending."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE transaction_reference = ?", (reference,))
        txn = cursor.fetchone()
        if not txn or txn["user_id"] != user["id"]:
            raise HTTPException(status_code=404, detail="Transaction reference not found")

        if not txn["is_verified"] and txn["payment_status"] == "Pending":
            lp = await livepay_transaction_status(reference, txn["currency"] or "USD")
            lp_status = ((lp or {}).get("status") or "").lower()
            if lp_status in LIVEPAY_SUCCESS_STATUSES:
                amount = (lp or {}).get("amount")
                activate_paid_license(conn, reference, float(amount) if amount not in (None, "") else None,
                                      (lp or {}).get("currency"), "status-check")
            elif lp_status in LIVEPAY_FAILED_STATUSES:
                _mark_failed(conn, reference, f"LivePay reported: {(lp or {}).get('message') or lp_status}")
            cursor.execute("SELECT * FROM transactions WHERE transaction_reference = ?", (reference,))
            txn = cursor.fetchone()

        return {
            "reference": reference,
            "payment_status": txn["payment_status"],
            "is_verified": bool(txn["is_verified"]),
            "amount": txn["amount"],
            "currency": txn["currency"],
            "license": get_license_status(user["id"], user.get("email") or "", conn),
        }
    finally:
        conn.close()


@router.post("/payments/card/process")
async def card_process_payment_removed():
    raise HTTPException(status_code=410, detail="Card details are no longer accepted here. Use /payments/card/checkout.")


@router.post("/payments/livepay/initiate")
async def livepay_initiate_removed():
    raise HTTPException(status_code=410, detail="Mobile money is not offered. Use /payments/card/checkout.")



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

def get_license_status(user_id: str, email: str, conn: Optional[Any] = None, product: str = "photobook") -> Dict[str, Any]:
    """The customer's current license, as the desktop apps should enforce it."""
    should_close = conn is None
    if conn is None:
        conn = get_db_connection()
    recalculate_all_subscription_statuses(conn)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.*, p.name AS pkg_name, p.product_access
        FROM subscriptions s
        LEFT JOIN packages p ON s.package_id = p.id
        WHERE s.user_id = ? AND s.payment_status = 'Verified'
          AND s.status IN ('Active', 'Expiring Soon', 'Lifetime')
        ORDER BY CASE WHEN s.expiry_date IS NULL THEN 1 ELSE 0 END DESC, s.expiry_date DESC
    """, (user_id,))
    rows = cursor.fetchall()
    if should_close:
        conn.close()

    product = normalize_product(product)
    for sub in rows:
        if access_includes(sub["product_access"], product):
            return {
                "active": True,
                "status": sub["status"],
                "packageId": sub["package_id"],
                "packageName": sub["pkg_name"] or sub["product_or_package"],
                "lifetime": sub["expiry_date"] is None,
                "expiryDate": sub["expiry_date"],
                "source": "admin" if (sub["payment_transaction_ref"] or "").startswith("ADMIN-") else "payment",
                "checkedAt": datetime.now(timezone.utc).isoformat(),
            }
    return {
        "active": False,
        "status": "No active license",
        "packageId": None,
        "packageName": None,
        "lifetime": False,
        "expiryDate": None,
        "source": None,
        "checkedAt": datetime.now(timezone.utc).isoformat(),
    }

# ---------- Email verification ----------
# Only enforced once SMTP is configured (otherwise nobody could ever receive a code).
EMAIL_CODE_MINUTES = 30
EMAIL_CODE_RESEND_SECONDS = 60
EMAIL_CODE_MAX_ATTEMPTS = 5
_email_code_attempts: Dict[str, int] = {}


def email_verification_required() -> bool:
    from email_service import is_smtp_configured
    return is_smtp_configured()


def _code_hash(user_id: str, code: str) -> str:
    import hashlib
    return hashlib.sha256(f"{user_id}:{code}".encode("utf-8")).hexdigest()


def _is_email_verified(conn, user_id: str) -> bool:
    row = conn.execute("SELECT email_verified, auth_provider FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row and (row["email_verified"] or row["auth_provider"] == "google"))


def _mongo_set_user(user_id: str, fields: Dict[str, Any]):
    try:
        db = get_mongo_db()
        if db is not None:
            db["users"].update_one({"_id": user_id}, {"$set": fields})
    except Exception as e:
        print(f"[MongoDB] user update error: {e}")


def issue_email_code(conn, user_id: str, email: str, full_name: str, purpose: str = "verify") -> bool:
    """Creates a fresh 6-digit code for the user and emails it. Returns whether it was sent.
    The same code proves the user owns the email, so it serves both verification and password reset."""
    import secrets
    code = f"{secrets.randbelow(1000000):06d}"
    now = datetime.now(timezone.utc)
    fields = {
        "email_code_hash": _code_hash(user_id, code),
        "email_code_expires": (now + timedelta(minutes=EMAIL_CODE_MINUTES)).isoformat(),
        "email_code_sent_at": now.isoformat(),
    }
    with conn:
        conn.execute("UPDATE users SET email_code_hash = ?, email_code_expires = ?, email_code_sent_at = ? WHERE id = ?",
                     (fields["email_code_hash"], fields["email_code_expires"], fields["email_code_sent_at"], user_id))
    _mongo_set_user(user_id, fields)
    _email_code_attempts.pop(user_id, None)
    if purpose == "reset":
        from email_service import send_password_reset_code
        return send_password_reset_code(email, full_name, code)
    from email_service import send_verification_code
    return send_verification_code(email, full_name, code)


def _hash_password(password: str):
    import hashlib
    salt = os.urandom(16).hex()
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 100000).hex(), salt


def _set_user_password(conn, user_id: str, password: str, extra: Optional[Dict[str, Any]] = None):
    """Stores a new password and signs the account out on every device."""
    pwhash, salt = _hash_password(password)
    now_iso = datetime.now(timezone.utc).isoformat()
    fields = {"password_hash": pwhash, "password_salt": salt, "updated_at": now_iso, **(extra or {})}
    cols = ", ".join(f"{k} = ?" for k in fields)
    with conn:
        conn.execute(f"UPDATE users SET {cols} WHERE id = ?", (*fields.values(), user_id))
    _mongo_set_user(user_id, fields)
    delete_user_sessions(user_id)


def _auth_response(user_id: str, email: str, name: str, auth_provider: str, conn, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    license_info = get_license_status(user_id, email, conn)
    user = {
        "id": user_id,
        "userId": user_id,
        "name": name,
        "fullName": name,
        "email": email,
        "authProvider": auth_provider,
        "status": "active" if license_info["active"] else "locked",
        "packageName": license_info["packageName"] or "No active license",
        "lifetimePro": license_info["lifetime"],
        "emailVerified": _is_email_verified(conn, user_id),
        "emailVerificationRequired": email_verification_required(),
    }
    if extra:
        user.update(extra)
    return {"success": True, "token": create_session(user_id, email), "user": user, "license": license_info}

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

    try:
        sent = issue_email_code(conn, user_id, email, full_name) if email_verification_required() else False
        resp = _auth_response(user_id, email, full_name, "email", conn)
        resp["verificationEmailSent"] = sent
        return resp
    finally:
        conn.close()

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

    display_name = user_record.get("full_name") or user_record.get("name") or email.split("@")[0].capitalize()
    try:
        return _auth_response(user_id, email, display_name, auth_prov, conn)
    finally:
        conn.close()

# Values copied from Google's console often pick up line breaks; IDs and secrets never contain whitespace.
GOOGLE_CLIENT_ID = "".join(os.environ.get("GOOGLE_CLIENT_ID", "").split())
GOOGLE_CLIENT_SECRET = "".join(os.environ.get("GOOGLE_CLIENT_SECRET", "").split())


@router.get("/auth/google/config")
async def google_auth_config():
    """Public: lets the desktop apps start Google sign-in without shipping the client ID."""
    return {"enabled": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET), "clientId": GOOGLE_CLIENT_ID}


# ---------- Google sign-in for the web admin console ----------
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://smart-ease-photobook-designer.onrender.com").rstrip("/")
ADMIN_GOOGLE_REDIRECT = f"{PUBLIC_BASE_URL}/api/v1/auth/google/callback"
_admin_google_states: Dict[str, Dict[str, Any]] = {}


@router.get("/auth/google/admin-start")
async def google_admin_start():
    """Sends the admin's browser to Google; Google returns to /auth/google/callback."""
    import base64, hashlib, secrets, time, urllib.parse
    from fastapi.responses import RedirectResponse
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        raise HTTPException(status_code=503, detail="Google sign-in is not set up on the server yet.")
    now = time.time()
    for s in [s for s, v in _admin_google_states.items() if now - v["created"] > 600]:
        _admin_google_states.pop(s, None)
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(16)
    _admin_google_states[state] = {"verifier": verifier, "created": now}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {
        "client_id": GOOGLE_CLIENT_ID, "redirect_uri": ADMIN_GOOGLE_REDIRECT, "response_type": "code",
        "scope": "openid email profile", "code_challenge": challenge, "code_challenge_method": "S256",
        "state": state, "prompt": "select_account",
    }
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params))


@router.get("/auth/google/callback")
async def google_admin_callback(code: str = Query(default=""), state: str = Query(default=""), error: str = Query(default="")):
    """Finishes admin Google sign-in and hands the session token to the console (URL fragment)."""
    import urllib.parse
    from fastapi.responses import RedirectResponse
    console = f"{PUBLIC_BASE_URL}/static/index.html"
    fail = lambda msg: RedirectResponse(f"{console}#admin_error={urllib.parse.quote(msg)}")
    pending = _admin_google_states.pop(state, None)
    if pending and pending.get("purpose") == "gmail":
        return await _finish_gmail_connect(code, error, pending, console)
    if error or not code:
        return fail("Google sign-in was cancelled.")
    if not pending:
        return fail("This sign-in link expired. Please try again.")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": ADMIN_GOOGLE_REDIRECT, "grant_type": "authorization_code",
            "code_verifier": pending["verifier"],
        })
    if resp.status_code != 200 or not resp.json().get("id_token"):
        print(f"[GoogleAuth] admin token exchange failed: {resp.status_code} {resp.text[:300]}")
        return fail("Google sign-in failed. Please try again.")
    try:
        result = await google_auth_user({"idToken": resp.json()["id_token"], "sourceApp": "Admin Console"})
    except HTTPException as e:
        return fail(str(e.detail))
    from security import ADMIN_EMAILS
    if (result.get("user", {}).get("email") or "").lower() not in ADMIN_EMAILS:
        delete_session(result["token"])
        return fail("This Google account is not an administrator.")
    return RedirectResponse(f"{console}#admin_token={urllib.parse.quote(result['token'])}")


# ---------- Admin's Gmail as the sender for password reset codes ----------

@router.get("/admin/gmail/status", dependencies=ADMIN)
async def gmail_status():
    import gmail_sender
    cfg = gmail_sender.load_config(force=True)
    return {"connected": bool(cfg), "email": (cfg or {}).get("email"), "connectedAt": (cfg or {}).get("connected_at")}


@router.post("/admin/gmail/connect", dependencies=ADMIN)
async def gmail_connect_start():
    """Returns the Google consent URL for gmail.send; Google returns to /auth/google/callback."""
    import base64, hashlib, secrets, time, urllib.parse
    import gmail_sender
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        raise HTTPException(status_code=503, detail="Google sign-in is not set up on the server yet.")
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(16)
    _admin_google_states[state] = {"verifier": verifier, "created": time.time(), "purpose": "gmail"}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {
        "client_id": GOOGLE_CLIENT_ID, "redirect_uri": ADMIN_GOOGLE_REDIRECT, "response_type": "code",
        "scope": f"openid email {gmail_sender.GMAIL_SCOPE}", "code_challenge": challenge,
        "code_challenge_method": "S256", "state": state,
        # offline + consent so Google hands out a refresh token every time.
        "access_type": "offline", "prompt": "consent select_account",
    }
    return {"url": "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)}


async def _finish_gmail_connect(code: str, error: str, pending: Dict[str, Any], console: str):
    import urllib.parse
    import gmail_sender
    from fastapi.responses import RedirectResponse
    done = lambda key, msg: RedirectResponse(f"{console}#{key}={urllib.parse.quote(msg)}")
    if error or not code:
        return done("gmail_error", "Gmail connection was cancelled.")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": ADMIN_GOOGLE_REDIRECT, "grant_type": "authorization_code",
            "code_verifier": pending["verifier"],
        })
    data = resp.json() if resp.status_code == 200 else {}
    if not data.get("refresh_token") or gmail_sender.GMAIL_SCOPE not in (data.get("scope") or ""):
        print(f"[Gmail] connect failed: {resp.status_code} {resp.text[:300]}")
        return done("gmail_error", "Google didn't allow sending email. Tick the 'Send email on your behalf' box and try again.")
    try:
        info = await _verify_google_id_token(data.get("id_token") or "")
    except HTTPException as e:
        return done("gmail_error", str(e.detail))
    gmail_sender.save_config(info["email"].lower(), data["refresh_token"], datetime.now(timezone.utc).isoformat())
    return done("gmail_connected", info["email"].lower())


@router.post("/admin/gmail/test", dependencies=ADMIN)
async def gmail_test(admin: Dict[str, Any] = Depends(require_admin)):
    import gmail_sender
    if not gmail_sender.is_configured():
        raise HTTPException(status_code=400, detail="Connect Gmail first.")
    to = admin.get("email") or ""
    ok = gmail_sender.send(to, "Smart Ease: Gmail is connected",
                           "Password reset codes for Smart Ease apps will be sent from this Gmail.",
                           "<p>Password reset codes for Smart Ease apps will be sent from this Gmail.</p>")
    if not ok:
        raise HTTPException(status_code=502, detail="Gmail refused to send. Try connecting Gmail again.")
    return {"success": True, "sentTo": to}


@router.post("/admin/gmail/disconnect", dependencies=ADMIN)
async def gmail_disconnect():
    import gmail_sender
    gmail_sender.clear_config()
    return {"success": True}


@router.post("/auth/google/exchange")
async def google_auth_exchange(payload: Dict[str, Any] = Body(...)):
    """
    Desktop Google sign-in: the app opened Google's consent page with PKCE and a loopback redirect,
    received the authorization code, and sends it here. The client secret never leaves the server.
    """
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        raise HTTPException(status_code=503, detail="Google sign-in is not set up on the server yet.")
    code = payload.get("code") or ""
    verifier = payload.get("codeVerifier") or payload.get("code_verifier") or ""
    redirect_uri = payload.get("redirectUri") or payload.get("redirect_uri") or ""
    if not code or not verifier:
        raise HTTPException(status_code=400, detail="Missing Google sign-in code.")
    if not redirect_uri.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise HTTPException(status_code=400, detail="Invalid redirect address.")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        })
    if resp.status_code != 200 or not resp.json().get("id_token"):
        print(f"[GoogleAuth] token exchange failed: {resp.status_code} {resp.text[:300]}")
        raise HTTPException(status_code=401, detail="Google sign-in failed. Please try again.")

    return await google_auth_user({
        "idToken": resp.json()["id_token"],
        "sourceApp": payload.get("sourceApp") or "Photobook",
        "deviceHostname": payload.get("deviceHostname"),
    })

async def _verify_google_id_token(id_token: str) -> Dict[str, Any]:
    """Asks Google to validate the ID token; identity fields come from Google, never from the client."""
    if not id_token:
        raise HTTPException(status_code=400, detail="Google sign-in token is required.")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": id_token})
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Google sign-in could not be verified.")
    info = resp.json()
    if GOOGLE_CLIENT_ID and info.get("aud") != GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=401, detail="Google sign-in was issued for a different app.")
    if str(info.get("email_verified")).lower() != "true":
        raise HTTPException(status_code=401, detail="Your Google email address is not verified.")
    return info

@router.post("/auth/google")
async def google_auth_user(payload: Dict[str, Any] = Body(...)):
    google_info = await _verify_google_id_token(payload.get("idToken") or payload.get("id_token") or "")
    email = (google_info.get("email") or "").strip().lower()
    name = (google_info.get("name") or payload.get("name") or payload.get("fullName") or "").strip()
    google_id = (google_info.get("sub") or "").strip()
    avatar_url = (google_info.get("picture") or payload.get("avatarUrl") or "").strip()
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

    try:
        return _auth_response(user_id, email, name, "google", conn, {"avatarUrl": avatar_url})
    finally:
        conn.close()


@router.get("/auth/me")
async def auth_me(product: str = Query(default="photobook"), device: Optional[str] = Query(default=None),
                  user: Dict[str, Any] = Depends(require_user)):
    """Current account and license. The desktop apps call this on start-up and periodically,
    which also marks the customer as online in the admin console."""
    product = normalize_product(product)
    try:
        record_presence_heartbeat(account_id=user["smart_ease_account_id"], product=product,
                                  mode="online", device_hostname=device)
    except Exception as e:
        print(f"[Presence] auth/me heartbeat warning: {e}")
    return {
        "success": True,
        "user": {
            "id": user["id"],
            "name": user.get("full_name"),
            "email": user.get("email"),
            "authProvider": user.get("auth_provider", "email"),
            "emailVerified": bool(user.get("email_verified")) or user.get("auth_provider") == "google",
            "emailVerificationRequired": email_verification_required(),
        },
        "license": get_license_status(user["id"], user.get("email") or "", product=product),
    }


@router.post("/auth/verify-email")
async def auth_verify_email(payload: Dict[str, Any] = Body(...), user: Dict[str, Any] = Depends(require_user)):
    code = str(payload.get("code") or "").strip()
    uid = user["id"]
    if user.get("email_verified"):
        return {"success": True, "emailVerified": True}
    if _email_code_attempts.get(uid, 0) >= EMAIL_CODE_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many wrong codes. Please request a new code.")
    expires = user.get("email_code_expires")
    if not user.get("email_code_hash") or not expires or datetime.fromisoformat(expires) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="This code has expired. Please request a new code.")
    import hmac
    if not hmac.compare_digest(_code_hash(uid, code), user["email_code_hash"]):
        _email_code_attempts[uid] = _email_code_attempts.get(uid, 0) + 1
        raise HTTPException(status_code=400, detail="That code is not correct. Please check the email and try again.")

    conn = get_db_connection()
    with conn:
        conn.execute("UPDATE users SET email_verified = 1, email_code_hash = NULL, email_code_expires = NULL WHERE id = ?", (uid,))
    conn.close()
    _mongo_set_user(uid, {"email_verified": 1, "email_code_hash": None, "email_code_expires": None})
    _email_code_attempts.pop(uid, None)
    return {"success": True, "emailVerified": True}


@router.post("/auth/resend-verification")
async def auth_resend_verification(user: Dict[str, Any] = Depends(require_user)):
    if user.get("email_verified"):
        return {"success": True, "emailVerified": True}
    if not email_verification_required():
        raise HTTPException(status_code=503, detail="Email sending is not set up yet. Please contact Smart Ease support.")
    sent_at = user.get("email_code_sent_at")
    if sent_at and (datetime.now(timezone.utc) - datetime.fromisoformat(sent_at)).total_seconds() < EMAIL_CODE_RESEND_SECONDS:
        raise HTTPException(status_code=429, detail="Please wait a minute before requesting another code.")
    conn = get_db_connection()
    try:
        sent = issue_email_code(conn, user["id"], user.get("email") or "", user.get("full_name") or "")
    finally:
        conn.close()
    if not sent:
        raise HTTPException(status_code=502, detail="We couldn't send the email right now. Please try again shortly.")
    return {"success": True, "sent": True}


def _find_user_by_email(conn, email: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,)).fetchone()
    if row:
        return dict(row)
    db = get_mongo_db()
    if db is not None:
        try:
            doc = db["users"].find_one({"email": email})
            if doc:
                doc = dict(doc)
                doc["id"] = doc.get("id") or doc.get("_id")
                return doc
        except Exception as e:
            print(f"[MongoDB] user lookup error: {e}")
    return None


@router.post("/auth/forgot-password")
async def auth_forgot_password(payload: Dict[str, Any] = Body(...)):
    """Emails a reset code. The answer is the same whether or not the account exists,
    so this can't be used to find out who has an account."""
    email = (payload.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    from email_service import can_send_email
    if not can_send_email():
        raise HTTPException(status_code=503, detail="Password reset by email isn't available yet. "
                            "If this is a Gmail address, use 'Continue with Google', or contact Smart Ease support to reset your password.")
    generic = {"success": True, "message": "If an account exists for this email, a reset code has been sent. Check your inbox and spam folder."}
    conn = get_db_connection()
    try:
        user = _find_user_by_email(conn, email)
        if not user:
            return generic
        sent_at = user.get("email_code_sent_at")
        if sent_at and (datetime.now(timezone.utc) - datetime.fromisoformat(sent_at)).total_seconds() < EMAIL_CODE_RESEND_SECONDS:
            raise HTTPException(status_code=429, detail="Please wait a minute before requesting another code.")
        if not issue_email_code(conn, user["id"], email, user.get("full_name") or "", purpose="reset"):
            raise HTTPException(status_code=502, detail="We couldn't send the email right now. Please try again shortly.")
        return generic
    finally:
        conn.close()


@router.post("/auth/reset-password")
async def auth_reset_password(payload: Dict[str, Any] = Body(...)):
    email = (payload.get("email") or "").strip().lower()
    code = str(payload.get("code") or "").strip()
    password = payload.get("password") or payload.get("newPassword") or ""
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long.")
    bad_code = HTTPException(status_code=400, detail="That code is not correct or has expired. Please request a new code.")
    conn = get_db_connection()
    try:
        user = _find_user_by_email(conn, email) if email else None
        if not user or not code:
            raise bad_code
        uid = user["id"]
        if _email_code_attempts.get(uid, 0) >= EMAIL_CODE_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="Too many wrong codes. Please request a new code.")
        expires = user.get("email_code_expires")
        import hmac
        if (not user.get("email_code_hash") or not expires
                or datetime.fromisoformat(expires) < datetime.now(timezone.utc)
                or not hmac.compare_digest(_code_hash(uid, code), user["email_code_hash"])):
            _email_code_attempts[uid] = _email_code_attempts.get(uid, 0) + 1
            raise bad_code
        # The code came from their inbox, so the email is confirmed too.
        _set_user_password(conn, uid, password, {"email_verified": 1, "email_code_hash": None, "email_code_expires": None})
        _email_code_attempts.pop(uid, None)
        return {"success": True, "message": "Your password has been changed. Please sign in with your new password."}
    finally:
        conn.close()


@router.post("/users/{user_id}/reset-password", dependencies=ADMIN)
async def admin_reset_password(user_id: str, payload: Dict[str, Any] = Body(default={})):
    """Admin sets a temporary password for a customer who forgot theirs (works without email set up)."""
    import secrets
    password = (payload.get("password") or "").strip() or f"SE-{secrets.token_hex(3).upper()}"
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long.")
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT id, email FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found.")
        _set_user_password(conn, user_id, password)
        return {"success": True, "email": row["email"], "temporaryPassword": password}
    finally:
        conn.close()


@router.post("/auth/logout")
async def auth_logout(user: Dict[str, Any] = Depends(require_user)):
    delete_session(user["_session_token"])
    return {"success": True}


# ==========================================
# 10. REAL-TIME LOGIN AUDIT & NOTIFICATION ENDPOINTS
# ==========================================

@router.post("/logins/record")
async def api_record_login(payload: Dict[str, Any] = Body(...), user: Dict[str, Any] = Depends(require_user)):
    """
    Records a user login from client desktop apps or web and dispatches a real-time security alert.
    """
    try:
        from database import record_user_login
        from email_service import dispatch_login_security_email

        # Only the signed-in account's own email, so this can't be used to send alerts to strangers.
        email = (user.get("email") or "").strip().lower()
        full_name = user.get("full_name") or payload.get("fullName") or payload.get("name")
        product = payload.get("product") or payload.get("sourceApp") or "Smart Ease Photobook Designer"
        device = payload.get("deviceHostname") or payload.get("device_hostname") or "Desktop PC"
        auth_method = payload.get("authMethod") or payload.get("auth_method") or "email"
        ip = payload.get("ipAddress") or payload.get("ip_address") or "127.0.0.1"
        user_id = user["id"]

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

@router.get("/logins/recent", dependencies=ADMIN)
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

@router.get("/users/{user_id}/logins", dependencies=ADMIN)
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
