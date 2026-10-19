"""
Smart Ease Subscription Manager - Database Layer
SQLite with WAL mode, robust transaction support, automatic status recalculation,
and verified transaction accounting.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Tuple

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "subscription_manager.db")

def get_db_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn

def init_db():
    conn = get_db_connection()
    with conn:
        # 1. Packages Table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS packages (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'USD',
                billing_type TEXT NOT NULL, -- 'recurring' or 'one_time'
                billing_interval TEXT NOT NULL, -- 'monthly', 'annual', 'lifetime'
                product_access TEXT NOT NULL, -- 'photobook', 'ease_select', 'both'
                description TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

        # 2. Users Table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                smart_ease_account_id TEXT UNIQUE NOT NULL,
                full_name TEXT NOT NULL,
                email TEXT,
                registered_date TEXT NOT NULL,
                product_owned TEXT NOT NULL, -- 'Smart Ease Photobook Designer', 'Ease Select', 'Both products', 'None'
                package_name TEXT,
                source_app TEXT DEFAULT 'Photobook',
                device_hostname TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

        # 3. Subscriptions Table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                package_id TEXT NOT NULL,
                product_or_package TEXT NOT NULL,
                start_date TEXT NOT NULL,
                expiry_date TEXT,
                billing_interval TEXT NOT NULL,
                status TEXT NOT NULL, -- 'Active', 'Expiring Soon', 'Expired', 'Cancelled', 'Payment Pending', 'Payment Failed', 'Lifetime'
                payment_status TEXT NOT NULL, -- 'Verified', 'Pending', 'Failed', 'Refunded'
                next_renewal_date TEXT,
                cancellation_date TEXT,
                last_successful_payment TEXT,
                payment_transaction_ref TEXT,
                amount_paid REAL NOT NULL DEFAULT 0.0,
                currency TEXT NOT NULL DEFAULT 'USD',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (package_id) REFERENCES packages(id)
            );
        """)

        # 4. Transactions Table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                transaction_date TEXT NOT NULL,
                user_id TEXT NOT NULL,
                subscription_id TEXT,
                package_id TEXT,
                product_or_package TEXT NOT NULL,
                amount REAL NOT NULL,
                refund_amount REAL NOT NULL DEFAULT 0.0,
                fee_amount REAL NOT NULL DEFAULT 0.0,
                net_amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'USD',
                payment_status TEXT NOT NULL, -- 'Verified', 'Pending', 'Failed', 'Refunded'
                payment_provider TEXT NOT NULL, -- 'Stripe', 'PayPal', 'Flutterwave', 'Pesapal', 'Manual', 'Bunny'
                transaction_reference TEXT UNIQUE NOT NULL,
                is_verified INTEGER NOT NULL DEFAULT 1,
                notes TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE SET NULL,
                FOREIGN KEY (package_id) REFERENCES packages(id) ON DELETE SET NULL
            );
        """)

        # 5. App Sync Logs Table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_sync_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_name TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                records_synced INTEGER NOT NULL,
                status TEXT NOT NULL,
                details TEXT
            );
        """)

        # Create helpful indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_subs_user_id ON subscriptions(user_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_subs_status ON subscriptions(status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_user_id ON transactions(user_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(transaction_date);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_status ON transactions(payment_status);")

    # Seed default packages if not exist
    seed_initial_packages(conn)
    # Check if we should seed default data
    seed_initial_data(conn)
    # Auto-recalculate subscription statuses
    recalculate_all_subscription_statuses(conn)
    conn.close()

def seed_initial_packages(conn: sqlite3.Connection):
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM packages")
    if cursor.fetchone()["cnt"] == 0:
        now = datetime.now(timezone.utc).isoformat()
        packages = [
            (
                "pkg_photobook",
                "Smart Ease Photobook Designer",
                2.45,
                "USD",
                "recurring",
                "monthly",
                "photobook",
                "Single product monthly subscription for Smart Ease Photobook Designer.",
                1,
                now,
                now
            ),
            (
                "pkg_bundle",
                "Smart Ease Photobook Designer + Ease Select",
                3.00,
                "USD",
                "recurring",
                "monthly",
                "both",
                "Dual suite monthly package for Photobook Designer and Ease Select.",
                1,
                now,
                now
            ),
            (
                "pkg_ease_select",
                "Ease Select",
                2.45,
                "USD",
                "recurring",
                "monthly",
                "ease_select",
                "Single product monthly subscription for Ease Select photo culling & synchronization.",
                1,
                now,
                now
            ),
            (
                "pkg_lifetime",
                "Lifetime Pro",
                200.00,
                "USD",
                "one_time",
                "lifetime",
                "both",
                "Permanent lifetime access licence for all Smart Ease products.",
                1,
                now,
                now
            )
        ]
        with conn:
            conn.executemany("""
                INSERT INTO packages (
                    id, name, price, currency, billing_type, billing_interval,
                    product_access, description, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, packages)

def seed_initial_data(conn: sqlite3.Connection):
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM users")
    if cursor.fetchone()["cnt"] > 0:
        return

    now = datetime.now(timezone.utc)
    
    # 1. Local Machine User: Rafia Pictures (from user_identity.json)
    # 2. Other realistic customers spanning diverse states
    sample_users = [
        {
            "id": "USR-10001",
            "smart_ease_account_id": "a6c0fee1d5422ef8",
            "full_name": "Rafia Pictures",
            "email": "studio@rafiapictures.com",
            "registered_date": (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S"),
            "product_owned": "Both products",
            "package_name": "Lifetime Pro",
            "source_app": "Photobook",
            "device_hostname": "DESKTOP-J6QB9L8",
            "notes": "Verified studio owner from local machine identity profile.",
            "sub": {
                "id": "SUB-20001",
                "package_id": "pkg_lifetime",
                "product_or_package": "Lifetime Pro",
                "start_date": (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S"),
                "expiry_date": None,
                "billing_interval": "lifetime",
                "payment_status": "Verified",
                "next_renewal_date": None,
                "cancellation_date": None,
                "last_successful_payment": (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S"),
                "payment_transaction_ref": "TXN-LIFETIME-RAFIA-001",
                "amount_paid": 200.00
            },
            "txn": {
                "id": "TXN-30001",
                "transaction_date": (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S"),
                "package_id": "pkg_lifetime",
                "product_or_package": "Lifetime Pro",
                "amount": 200.00,
                "refund_amount": 0.0,
                "fee_amount": 5.80,
                "payment_status": "Verified",
                "payment_provider": "Stripe",
                "transaction_reference": "ch_3Pq9LifRafia001",
                "notes": "Lifetime Pro license purchase for full studio."
            }
        },
        {
            "id": "USR-10002",
            "smart_ease_account_id": "SE-91823",
            "full_name": "Sarah Jenkins",
            "email": "sarah.jenkins@lumina-weddings.com",
            "registered_date": (now - timedelta(days=25)).strftime("%Y-%m-%d %H:%M:%S"),
            "product_owned": "Smart Ease Photobook Designer",
            "package_name": "Smart Ease Photobook Designer",
            "source_app": "Photobook",
            "device_hostname": "MACBOOK-PRO-SJ",
            "notes": "Active monthly subscriber.",
            "sub": {
                "id": "SUB-20002",
                "package_id": "pkg_photobook",
                "product_or_package": "Smart Ease Photobook Designer",
                "start_date": (now - timedelta(days=25)).strftime("%Y-%m-%d %H:%M:%S"),
                "expiry_date": (now + timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S"), # Expiring soon
                "billing_interval": "monthly",
                "payment_status": "Verified",
                "next_renewal_date": (now + timedelta(days=5)).strftime("%Y-%m-%d"),
                "cancellation_date": None,
                "last_successful_payment": (now - timedelta(days=25)).strftime("%Y-%m-%d %H:%M:%S"),
                "payment_transaction_ref": "ch_3PzPhotoSarah01",
                "amount_paid": 2.45
            },
            "txn": {
                "id": "TXN-30002",
                "transaction_date": (now - timedelta(days=25)).strftime("%Y-%m-%d %H:%M:%S"),
                "package_id": "pkg_photobook",
                "product_or_package": "Smart Ease Photobook Designer",
                "amount": 2.45,
                "refund_amount": 0.0,
                "fee_amount": 0.20,
                "payment_status": "Verified",
                "payment_provider": "Stripe",
                "transaction_reference": "ch_3PzPhotoSarah01",
                "notes": "Monthly subscription charge."
            }
        },
        {
            "id": "USR-10003",
            "smart_ease_account_id": "SE-54210",
            "full_name": "David Ochieng",
            "email": "david.ochieng@kreative.ug",
            "registered_date": (now - timedelta(days=35)).strftime("%Y-%m-%d %H:%M:%S"),
            "product_owned": "Ease Select",
            "package_name": "Ease Select",
            "source_app": "Ease Select",
            "device_hostname": "WIN-STUDIO-DAVID",
            "notes": "Subscription expired 5 days ago.",
            "sub": {
                "id": "SUB-20003",
                "package_id": "pkg_ease_select",
                "product_or_package": "Ease Select",
                "start_date": (now - timedelta(days=35)).strftime("%Y-%m-%d %H:%M:%S"),
                "expiry_date": (now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S"), # Expired
                "billing_interval": "monthly",
                "payment_status": "Verified",
                "next_renewal_date": None,
                "cancellation_date": None,
                "last_successful_payment": (now - timedelta(days=35)).strftime("%Y-%m-%d %H:%M:%S"),
                "payment_transaction_ref": "flw_ref_ochieng_01",
                "amount_paid": 2.45
            },
            "txn": {
                "id": "TXN-30003",
                "transaction_date": (now - timedelta(days=35)).strftime("%Y-%m-%d %H:%M:%S"),
                "package_id": "pkg_ease_select",
                "product_or_package": "Ease Select",
                "amount": 2.45,
                "refund_amount": 0.0,
                "fee_amount": 0.15,
                "payment_status": "Verified",
                "payment_provider": "Flutterwave",
                "transaction_reference": "flw_ref_ochieng_01",
                "notes": "Initial monthly subscription."
            }
        },
        {
            "id": "USR-10004",
            "smart_ease_account_id": "SE-33829",
            "full_name": "Michael Chen",
            "email": "mchen@aperturephoto.io",
            "registered_date": (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
            "product_owned": "Both products",
            "package_name": "Smart Ease Photobook Designer + Ease Select",
            "source_app": "Photobook",
            "device_hostname": "CHEN-DESKTOP",
            "notes": "Signed up today with dual bundle.",
            "sub": {
                "id": "SUB-20004",
                "package_id": "pkg_bundle",
                "product_or_package": "Smart Ease Photobook Designer + Ease Select",
                "start_date": (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
                "expiry_date": (now + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"), # Active
                "billing_interval": "monthly",
                "payment_status": "Verified",
                "next_renewal_date": (now + timedelta(days=30)).strftime("%Y-%m-%d"),
                "cancellation_date": None,
                "last_successful_payment": (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
                "payment_transaction_ref": "pp_txn_chen_today_99",
                "amount_paid": 3.00
            },
            "txn": {
                "id": "TXN-30004",
                "transaction_date": (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
                "package_id": "pkg_bundle",
                "product_or_package": "Smart Ease Photobook Designer + Ease Select",
                "amount": 3.00,
                "refund_amount": 0.0,
                "fee_amount": 0.25,
                "payment_status": "Verified",
                "payment_provider": "PayPal",
                "transaction_reference": "pp_txn_chen_today_99",
                "notes": "Today verified dual bundle subscription."
            }
        },
        {
            "id": "USR-10005",
            "smart_ease_account_id": "SE-11204",
            "full_name": "Elena Rostova",
            "email": "elena@rostovaphoto.com",
            "registered_date": (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S"),
            "product_owned": "None",
            "package_name": None,
            "source_app": "Photobook",
            "device_hostname": "ELENA-PC",
            "notes": "Registered account, has not purchased any license yet.",
            "sub": None,
            "txn": None
        },
        {
            "id": "USR-10006",
            "smart_ease_account_id": "SE-77401",
            "full_name": "Marcus Vance",
            "email": "vance@cinematicmoments.co",
            "registered_date": (now - timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S"),
            "product_owned": "Smart Ease Photobook Designer",
            "package_name": "Smart Ease Photobook Designer",
            "source_app": "Photobook",
            "device_hostname": "VANCE-STUDIO",
            "notes": "Monthly subscriber with active status.",
            "sub": {
                "id": "SUB-20005",
                "package_id": "pkg_photobook",
                "product_or_package": "Smart Ease Photobook Designer",
                "start_date": (now - timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S"),
                "expiry_date": (now + timedelta(days=24)).strftime("%Y-%m-%d %H:%M:%S"), # Active
                "billing_interval": "monthly",
                "payment_status": "Verified",
                "next_renewal_date": (now + timedelta(days=24)).strftime("%Y-%m-%d"),
                "cancellation_date": None,
                "last_successful_payment": (now - timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S"),
                "payment_transaction_ref": "stripe_vance_661",
                "amount_paid": 2.45
            },
            "txn": {
                "id": "TXN-30005",
                "transaction_date": (now - timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S"),
                "package_id": "pkg_photobook",
                "product_or_package": "Smart Ease Photobook Designer",
                "amount": 2.45,
                "refund_amount": 0.0,
                "fee_amount": 0.20,
                "payment_status": "Verified",
                "payment_provider": "Stripe",
                "transaction_reference": "stripe_vance_661",
                "notes": "Verified subscription purchase."
            }
        },
        {
            "id": "USR-10007",
            "smart_ease_account_id": "SE-88219",
            "full_name": "Amina Al-Mansoor",
            "email": "amina@desertbloom.ae",
            "registered_date": (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
            "product_owned": "Both products",
            "package_name": "Lifetime Pro",
            "source_app": "Photobook",
            "device_hostname": "AMINA-MACBOOK",
            "notes": "Lifetime Pro customer verified this week.",
            "sub": {
                "id": "SUB-20006",
                "package_id": "pkg_lifetime",
                "product_or_package": "Lifetime Pro",
                "start_date": (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
                "expiry_date": None,
                "billing_interval": "lifetime",
                "payment_status": "Verified",
                "next_renewal_date": None,
                "cancellation_date": None,
                "last_successful_payment": (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
                "payment_transaction_ref": "stripe_amina_life_772",
                "amount_paid": 200.00
            },
            "txn": {
                "id": "TXN-30006",
                "transaction_date": (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
                "package_id": "pkg_lifetime",
                "product_or_package": "Lifetime Pro",
                "amount": 200.00,
                "refund_amount": 0.0,
                "fee_amount": 5.80,
                "payment_status": "Verified",
                "payment_provider": "Stripe",
                "transaction_reference": "stripe_amina_life_772",
                "notes": "Lifetime Pro purchase."
            }
        },
        {
            "id": "USR-10008",
            "smart_ease_account_id": "SE-44012",
            "full_name": "Lucas Morales",
            "email": "lucas@moraleslens.com",
            "registered_date": (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S"),
            "product_owned": "Ease Select",
            "package_name": "Ease Select",
            "source_app": "Ease Select",
            "device_hostname": "MORALES-WORKSTATION",
            "notes": "Verified Ease Select subscriber from today.",
            "sub": {
                "id": "SUB-20007",
                "package_id": "pkg_ease_select",
                "product_or_package": "Ease Select",
                "start_date": (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S"),
                "expiry_date": (now + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"), # Active
                "billing_interval": "monthly",
                "payment_status": "Verified",
                "next_renewal_date": (now + timedelta(days=30)).strftime("%Y-%m-%d"),
                "cancellation_date": None,
                "last_successful_payment": (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S"),
                "payment_transaction_ref": "ch_3PqEaseSelectLucas01",
                "amount_paid": 2.45
            },
            "txn": {
                "id": "TXN-30007",
                "transaction_date": (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S"),
                "package_id": "pkg_ease_select",
                "product_or_package": "Ease Select",
                "amount": 2.45,
                "refund_amount": 0.0,
                "fee_amount": 0.20,
                "payment_status": "Verified",
                "payment_provider": "Stripe",
                "transaction_reference": "ch_3PqEaseSelectLucas01",
                "notes": "Ease Select monthly subscription verified today."
            }
        }
    ]

    with conn:
        for u in sample_users:
            conn.execute("""
                INSERT INTO users (
                    id, smart_ease_account_id, full_name, email, registered_date,
                    product_owned, package_name, source_app, device_hostname, notes,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                u["id"], u["smart_ease_account_id"], u["full_name"], u["email"],
                u["registered_date"], u["product_owned"], u["package_name"],
                u["source_app"], u["device_hostname"], u["notes"],
                u["registered_date"], u["registered_date"]
            ))

            if u["sub"]:
                s = u["sub"]
                conn.execute("""
                    INSERT INTO subscriptions (
                        id, user_id, package_id, product_or_package, start_date, expiry_date,
                        billing_interval, status, payment_status, next_renewal_date,
                        cancellation_date, last_successful_payment, payment_transaction_ref,
                        amount_paid, currency, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Active', ?, ?, ?, ?, ?, ?, 'USD', ?, ?)
                """, (
                    s["id"], u["id"], s["package_id"], s["product_or_package"],
                    s["start_date"], s["expiry_date"], s["billing_interval"],
                    s["payment_status"], s["next_renewal_date"], s["cancellation_date"],
                    s["last_successful_payment"], s["payment_transaction_ref"],
                    s["amount_paid"], s["start_date"], s["start_date"]
                ))

            if u["txn"]:
                t = u["txn"]
                net = t["amount"] - t["refund_amount"] - t["fee_amount"]
                conn.execute("""
                    INSERT INTO transactions (
                        id, transaction_date, user_id, subscription_id, package_id,
                        product_or_package, amount, refund_amount, fee_amount, net_amount,
                        currency, payment_status, payment_provider, transaction_reference,
                        is_verified, notes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'USD', ?, ?, ?, 1, ?, ?)
                """, (
                    t["id"], t["transaction_date"], u["id"],
                    u["sub"]["id"] if u["sub"] else None, t["package_id"],
                    t["product_or_package"], t["amount"], t["refund_amount"],
                    t["fee_amount"], net, t["payment_status"], t["payment_provider"],
                    t["transaction_reference"], t["notes"], t["transaction_date"]
                ))

def recalculate_all_subscription_statuses(conn: Optional[sqlite3.Connection] = None):
    """
    Evaluates real-time subscription status according to rules:
    - If payment_status != 'Verified' -> 'Payment Pending' or 'Payment Failed'
    - If cancellation_date is set -> 'Cancelled'
    - If billing_interval == 'lifetime' or package is one_time -> 'Lifetime'
    - If now > expiry_date -> 'Expired'
    - If expiry_date <= now + 7 days -> 'Expiring Soon'
    - Else -> 'Active'
    """
    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    now = datetime.now(timezone.utc)
    now_naive = datetime.now()

    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.id, s.expiry_date, s.billing_interval, s.payment_status,
               s.cancellation_date, p.billing_type
        FROM subscriptions s
        LEFT JOIN packages p ON s.package_id = p.id
    """)
    rows = cursor.fetchall()

    updates = []
    for r in rows:
        sub_id = r["id"]
        exp_str = r["expiry_date"]
        billing_int = (r["billing_interval"] or "").lower()
        billing_type = (r["billing_type"] or "").lower()
        pay_status = r["payment_status"]
        cancel_date = r["cancellation_date"]

        status = "Active"
        if pay_status == "Pending":
            status = "Payment Pending"
        elif pay_status == "Failed":
            status = "Payment Failed"
        elif pay_status == "Refunded":
            status = "Cancelled"
        elif cancel_date:
            status = "Cancelled"
        elif billing_int == "lifetime" or billing_type == "one_time" or not exp_str:
            status = "Lifetime"
        else:
            # Parse expiry date
            try:
                # Handle ISO formats or standard YYYY-MM-DD HH:MM:SS
                exp_dt = None
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                    try:
                        exp_dt = datetime.strptime(exp_str.split(".")[0], fmt)
                        break
                    except ValueError:
                        continue
                if exp_dt:
                    diff_days = (exp_dt - now_naive).total_seconds() / 86400.0
                    if diff_days < 0:
                        status = "Expired"
                    elif diff_days <= 7.0:
                        status = "Expiring Soon"
                    else:
                        status = "Active"
            except Exception:
                status = "Active"

        updates.append((status, now.isoformat(), sub_id))

    with conn:
        conn.executemany("""
            UPDATE subscriptions
            SET status = ?, updated_at = ?
            WHERE id = ?
        """, updates)

    if should_close:
        conn.close()
