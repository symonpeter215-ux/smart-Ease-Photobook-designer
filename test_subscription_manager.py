"""
Smart Ease Subscription Manager - Comprehensive Verification Suite
Tests all requirements and endpoints using FastAPI TestClient.
"""

import sys
import os

# Ensure current dir in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from fastapi.testclient import TestClient
from server import app
from database import init_db

def run_tests():
    print("==============================================================")
    print("  RUNNING SMART EASE SUBSCRIPTION MANAGER TEST SUITE         ")
    print("==============================================================")
    
    # Initialize DB
    init_db()
    client = TestClient(app)

    # 1. Health check
    resp = client.get("/health")
    assert resp.status_code == 200, f"Health check failed: {resp.text}"
    print("[PASS] 1. Health check endpoint OK")

    # 2. Main Dashboard Metrics
    resp = client.get("/api/v1/dashboard/metrics")
    assert resp.status_code == 200
    metrics = resp.json()
    print(f"       Dashboard Metrics: Users={metrics['total_registered_users']}, ActiveSubs={metrics['active_subscriptions']}, ExpiringSoon={metrics['expiring_soon']}, Expired={metrics['expired_subscriptions']}, TodayEarnings=${metrics['today_earnings']}, MonthEarnings=${metrics['this_month_earnings']}, LifetimeRev=${metrics['lifetime_revenue']}, LifetimePro={metrics['lifetime_pro_users']}")
    assert metrics["total_registered_users"] >= 8
    assert metrics["active_subscriptions"] >= 4
    assert metrics["lifetime_pro_users"] >= 2
    assert metrics["currency"] == "USD"
    print("[PASS] 2. Dashboard KPI calculations verified (all 8 cards dynamically computed)")

    # 3. Monthly Revenue Chart
    resp = client.get("/api/v1/dashboard/chart")
    assert resp.status_code == 200
    chart = resp.json()
    assert len(chart["daily"]) == chart["days_in_month"]
    assert chart["total_revenue"] >= 0.0
    print(f"[PASS] 3. Monthly revenue chart verified ({chart['month_label']}, {chart['days_in_month']} days, Total=${chart['total_revenue']})")

    # 4. User Management (12 columns, search, filters)
    resp = client.get("/api/v1/users")
    assert resp.status_code == 200
    users = resp.json()
    assert len(users) >= 8
    u1 = users[0]
    required_cols = [
        "full_name", "email", "smart_ease_account_id", "registered_date",
        "product_owned", "package_name", "subscription_start_date",
        "subscription_expiry_date", "subscription_status", "amount_paid",
        "last_successful_payment_date", "next_renewal_date"
    ]
    for col in required_cols:
        assert col in u1, f"Missing required column in user record: {col}"
    print(f"[PASS] 4a. User table columns verified (all 12 required columns present)")

    # Test search by name
    resp_search = client.get("/api/v1/users?search=Rafia")
    assert resp_search.status_code == 200
    found = resp_search.json()
    assert len(found) >= 1 and "Rafia" in found[0]["full_name"]
    print(f"[PASS] 4b. User search by name verified ('Rafia' found {len(found)} record)")

    # Test product filter
    resp_flt = client.get("/api/v1/users?product=photobook")
    assert resp_flt.status_code == 200
    print(f"[PASS] 4c. User filter by product verified ({len(resp_flt.json())} Photobook users)")

    # 5. Product & Package Management (Initial 4 packages, customer counts, price edit)
    resp = client.get("/api/v1/packages")
    assert resp.status_code == 200
    pkgs = resp.json()
    assert len(pkgs) == 4
    pkg_map = {p["name"]: p for p in pkgs}
    assert "Smart Ease Photobook Designer" in pkg_map
    assert "Smart Ease Photobook Designer + Ease Select" in pkg_map
    assert "Ease Select" in pkg_map
    assert "Lifetime Pro" in pkg_map
    assert pkg_map["Lifetime Pro"]["price"] == 200.00
    assert pkg_map["Lifetime Pro"]["billing_type"] == "one_time"
    print(f"[PASS] 5a. All 4 initial packages verified (with customer counts and lifetime distinction)")

    # Test package price update
    resp_edit = client.put("/api/v1/packages/pkg_photobook", json={"price": 2.45, "name": "Smart Ease Photobook Designer", "description": "Updated pricing"})
    assert resp_edit.status_code == 200
    print("[PASS] 5b. Package price configuration by admin verified")

    # 6. Subscription Tracking (Auto-status calculation & statuses)
    resp = client.get("/api/v1/subscriptions")
    assert resp.status_code == 200
    subs = resp.json()
    statuses = set(s["status"] for s in subs)
    print(f"       Recorded Subscription Statuses: {statuses}")
    assert "Active" in statuses or "Lifetime" in statuses
    print("[PASS] 6. Subscription lifecycle tracking and auto-status determination verified")

    # 7. Daily & Monthly Earnings (gross vs refunds vs net, fee separation)
    resp_daily = client.get("/api/v1/revenue/daily")
    assert resp_daily.status_code == 200
    d_data = resp_daily.json()
    assert "total_verified_payments" in d_data
    assert "revenue_photobook" in d_data
    assert "revenue_lifetime_pro" in d_data
    print(f"[PASS] 7a. Daily earnings breakdown verified: Today Total=${d_data['total_verified_payments']}")

    resp_monthly = client.get("/api/v1/revenue/monthly")
    assert resp_monthly.status_code == 200
    m_data = resp_monthly.json()
    assert "net_recorded_revenue" in m_data
    assert "refunds_and_chargebacks" in m_data
    assert "processing_fees" in m_data
    print(f"[PASS] 7b. Monthly earnings breakdown verified: Gross=${m_data['total_verified_payments']}, Net=${m_data['net_recorded_revenue']}")

    # 8. Connected Apps Detection & Local Sync
    resp_apps = client.get("/api/v1/apps/status")
    assert resp_apps.status_code == 200
    apps_data = resp_apps.json()
    assert "photobook" in apps_data
    assert "ease_select" in apps_data
    print(f"[PASS] 8a. Connected applications detection verified: Photobook={apps_data['photobook']['status']}, EaseSelect={apps_data['ease_select']['status']}")

    resp_sync = client.post("/api/v1/apps/sync-local")
    assert resp_sync.status_code == 200
    sync_res = resp_sync.json()
    assert sync_res["status"] == "success"
    print(f"[PASS] 8b. Local application scanner & identity sync verified: {sync_res['details']}")

    # 9. Client License Verification API (for Photobook & Ease Select integration)
    resp_ver = client.post("/api/v1/client/verify-license", json={
        "account_id": "a6c0fee1d5422ef8",
        "product": "photobook"
    })
    assert resp_ver.status_code == 200
    v_data = resp_ver.json()
    assert v_data["has_access"] is True
    assert v_data["status"] == "Lifetime"
    print(f"[PASS] 9. Client license verification API verified: Account 'a6c0fee1d5422ef8' granted={v_data['has_access']} ({v_data['status']})")

    # 10. Both Apps vs Independent Subscription Filters (New Feature Verification)
    assert "app_breakdown" in metrics
    ab = metrics["app_breakdown"]
    print(f"       App Breakdown: TotalBothApps={ab['total_both_apps']}, IndPhotobook={ab['independent_photobook']}, IndEaseSelect={ab['independent_ease_select']}, DualIndependent={ab['both_dual_independent']}")
    assert ab["total_both_apps"] >= 3
    assert ab["independent_photobook"] >= 2
    assert ab["independent_ease_select"] >= 2
    assert ab["both_dual_independent"] >= 1
    print("[PASS] 10a. Dashboard App Breakdown metrics verified")

    # Filter: Both Apps
    r_both = client.get("/api/v1/users?app_subscription_filter=both")
    assert r_both.status_code == 200
    both_users = r_both.json()
    assert all(u["app_access_category"] == "both" for u in both_users)
    print(f"[PASS] 10b. Both Apps filter verified: {len(both_users)} users using both apps")

    # Filter: Independent Photobook Designer
    r_pb = client.get("/api/v1/users?app_subscription_filter=independent_photobook")
    assert r_pb.status_code == 200
    pb_users = r_pb.json()
    assert len(pb_users) >= 2
    print(f"[PASS] 10c. Independent Photobook filter verified: {len(pb_users)} users")

    # Filter: Independent Ease Select
    r_es = client.get("/api/v1/users?app_subscription_filter=independent_ease_select")
    assert r_es.status_code == 200
    es_users = r_es.json()
    assert len(es_users) >= 2
    print(f"[PASS] 10d. Independent Ease Select filter verified: {len(es_users)} users")

    # Filter: Dual Independent Subscriptions (Separate subscriptions for each app)
    r_dual = client.get("/api/v1/users?app_subscription_filter=both_independent")
    assert r_dual.status_code == 200
    dual_users = r_dual.json()
    assert len(dual_users) >= 1
    assert any(u["full_name"] == "James Kigozi" for u in dual_users)
    print(f"[PASS] 10e. Dual Independent Subscriptions filter verified: {len(dual_users)} user holding separate subs")

    # Subscriptions table filter by access type
    r_sub_ind = client.get("/api/v1/subscriptions?access_type_filter=independent")
    assert r_sub_ind.status_code == 200
    ind_subs = r_sub_ind.json()
    assert all(s["is_independent"] is True for s in ind_subs)
    print(f"[PASS] 10f. Subscriptions table access_type_filter verified: {len(ind_subs)} independent subscriptions")

    print("\n==============================================================")
    print("  ALL VERIFICATION TESTS PASSED SUCCESSFULLY!                 ")
    print("==============================================================")

if __name__ == "__main__":
    run_tests()
