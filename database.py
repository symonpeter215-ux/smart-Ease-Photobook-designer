"""
Smart Ease Subscription Manager - Database Layer
SQLite with WAL mode, robust transaction support, automatic status recalculation,
and verified transaction accounting.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Tuple
from mongo_db import sync_mongo_to_sqlite, sync_sqlite_to_mongo

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
                connection_mode TEXT NOT NULL DEFAULT 'online', -- 'online' or 'offline_mode'
                last_seen TEXT,
                last_ip TEXT DEFAULT '127.0.0.1',
                last_app TEXT,
                app_version TEXT DEFAULT 'v2.4',
                offline_lease_until TEXT,
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

        # 6. User Presence & Connectivity Logs Table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_presence_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                event_type TEXT NOT NULL, -- 'heartbeat', 'license_check', 'mode_switch', 'login'
                connection_mode TEXT NOT NULL DEFAULT 'online', -- 'online', 'offline_mode'
                source_app TEXT NOT NULL DEFAULT 'Photobook',
                device_hostname TEXT,
                ip_address TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

                # 7. User Logins & Security Activity Table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_logins (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                email TEXT NOT NULL,
                full_name TEXT,
                product TEXT NOT NULL DEFAULT 'Smart Ease Photobook Designer',
                device_hostname TEXT,
                ip_address TEXT,
                auth_method TEXT NOT NULL DEFAULT 'email',
                login_time TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'SUCCESS',
                alert_dispatched INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logins_email ON user_logins(email);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logins_time ON user_logins(login_time);")

        # Create helpful indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_subs_user_id ON subscriptions(user_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_subs_status ON subscriptions(status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_user_id ON transactions(user_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(transaction_date);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_status ON transactions(payment_status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pres_user_id ON user_presence_logs(user_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pres_created ON user_presence_logs(created_at);")

    # Migrate columns if existing DB was created without them
    migrate_presence_columns(conn)
    migrate_user_auth_columns(conn)

    # Seed default packages if not exist
    seed_initial_packages(conn)
    # (Demo customers from seed_initial_data are no longer created: they showed up as real
    # customers and revenue in the admin console.)
    # Sync from MongoDB cloud if MONGODB_URI is provided
    sync_mongo_to_sqlite(conn)
    # Auto-recalculate subscription statuses
    recalculate_all_subscription_statuses(conn)
    # Ensure MongoDB cloud has all latest records
    sync_sqlite_to_mongo(conn)
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

    sync_sqlite_to_mongo(conn)

    if should_close:
        conn.close()

def migrate_presence_columns(conn: sqlite3.Connection):
    """
    Safely migrates SQLite schema if the existing users table does not have presence columns.
    Seeds realistic presence states for initial users if they are newly added.
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(users)")
    cols = {row["name"] for row in cursor.fetchall()}
    
    with conn:
        if "connection_mode" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN connection_mode TEXT NOT NULL DEFAULT 'online'")
        if "last_seen" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_seen TEXT")
        if "last_ip" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_ip TEXT DEFAULT '127.0.0.1'")
        if "last_app" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_app TEXT")
        if "app_version" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN app_version TEXT DEFAULT 'v2.4'")
        if "offline_lease_until" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN offline_lease_until TEXT")

    # If all last_seen are NULL, seed realistic states for default users
    cursor.execute("SELECT COUNT(*) as cnt FROM users WHERE last_seen IS NOT NULL")
    if cursor.fetchone()["cnt"] == 0:
        now_dt = datetime.now(timezone.utc)
        defaults = [
            ("a6c0fee1d5422ef8", "online", (now_dt - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S"), "Smart Ease Photobook Designer", None),
            ("SE-33829", "online", (now_dt - timedelta(minutes=4)).strftime("%Y-%m-%d %H:%M:%S"), "Photobook + Ease Select", None),
            ("SE-67129", "online", (now_dt - timedelta(minutes=7)).strftime("%Y-%m-%d %H:%M:%S"), "Ease Select", None),
            ("SE-54210", "offline_mode", (now_dt - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"), "Ease Select", (now_dt + timedelta(days=27)).strftime("%Y-%m-%d %H:%M:%S")),
            ("SE-77401", "offline_mode", (now_dt - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"), "Smart Ease Photobook Designer", (now_dt + timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")),
            ("SE-91823", "online", (now_dt - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"), "Smart Ease Photobook Designer", None),
            ("SE-44012", "online", (now_dt - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S"), "Ease Select", None),
            ("SE-88219", "online", (now_dt - timedelta(days=12)).strftime("%Y-%m-%d %H:%M:%S"), "Photobook", None),
            ("SE-11204", "online", None, "Photobook", None),
        ]
        with conn:
            for acc_id, mode, seen, app_name, lease in defaults:
                conn.execute("""
                    UPDATE users 
                    SET connection_mode = ?, last_seen = ?, last_app = ?, offline_lease_until = ?
                    WHERE smart_ease_account_id = ?
                """, (mode, seen, app_name, lease, acc_id))

def migrate_user_auth_columns(conn: sqlite3.Connection):
    """
    Safely migrates SQLite schema if the existing users table does not have authentication columns.
    Ensures password_hash, password_salt, auth_provider, google_id, and avatar_url exist.
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(users)")
    cols = {row["name"] for row in cursor.fetchall()}
    with conn:
        if "password_hash" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        if "password_salt" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN password_salt TEXT")
        if "auth_provider" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN auth_provider TEXT DEFAULT 'email'")
        if "google_id" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN google_id TEXT")
        if "avatar_url" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT")
        if "email_verified" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
        if "email_code_hash" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email_code_hash TEXT")
        if "email_code_expires" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email_code_expires TEXT")
        if "email_code_sent_at" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email_code_sent_at TEXT")

    # Card payments buy several months at once; the paid access length is stored on the transaction.
    cursor.execute("PRAGMA table_info(transactions)")
    tx_cols = {row["name"] for row in cursor.fetchall()}
    if "access_days" not in tx_cols:
        with conn:
            conn.execute("ALTER TABLE transactions ADD COLUMN access_days INTEGER")

def record_presence_heartbeat(
    account_id: str,
    product: str = "photobook",
    mode: str = "online",
    device_hostname: Optional[str] = None,
    ip_address: str = "127.0.0.1",
    app_version: str = "v2.4",
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Records an active heartbeat from Photobook Designer or Ease Select.
    Updates the user's presence, last seen timestamp, active client app, and connection mode.
    """
    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.cursor()
    cursor.execute("SELECT id, smart_ease_account_id, full_name, connection_mode FROM users WHERE smart_ease_account_id = ? OR email = ? OR id = ?", (account_id, account_id, account_id))
    user = cursor.fetchone()

    if not user:
        if should_close:
            conn.close()
        return {"status": "user_not_found", "recorded": False}

    prod_label = "Smart Ease Photobook Designer" if "photobook" in product.lower() else ("Ease Select" if "select" in product.lower() else product)
    actual_mode = mode if mode in ("online", "offline_mode") else (user["connection_mode"] or "online")

    with conn:
        conn.execute("""
            UPDATE users
            SET last_seen = ?, last_ip = ?, last_app = ?, connection_mode = ?,
                device_hostname = COALESCE(?, device_hostname), app_version = ?, updated_at = ?
            WHERE id = ?
        """, (now_iso, ip_address, prod_label, actual_mode, device_hostname, app_version, now_iso, user["id"]))

        conn.execute("""
            INSERT INTO user_presence_logs (
                user_id, account_id, event_type, connection_mode, source_app, device_hostname, ip_address, created_at
            ) VALUES (?, ?, 'heartbeat', ?, ?, ?, ?, ?)
        """, (user["id"], user["smart_ease_account_id"], actual_mode, prod_label, device_hostname, ip_address, now_iso))

    # Heartbeats are frequent: update just this user in MongoDB instead of re-syncing every table.
    try:
        from mongo_db import get_mongo_db
        mdb = get_mongo_db()
        if mdb is not None:
            mdb["users"].update_one({"_id": user["id"]}, {"$set": {
                "last_seen": now_iso, "last_ip": ip_address, "last_app": prod_label,
                "connection_mode": actual_mode, "app_version": app_version, "updated_at": now_iso,
            }})
    except Exception as e:
        print(f"[MongoDB] presence update error: {e}")

    res = {
        "status": "success",
        "recorded": True,
        "user_id": user["id"],
        "account_id": user["smart_ease_account_id"],
        "presence": "online",
        "connection_mode": actual_mode,
        "last_seen": now_iso
    }

    if should_close:
        conn.close()
    return res

def set_user_connection_mode(
    user_id: str,
    mode: str,
    lease_days: int = 30,
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Switches a customer between Online Mode and Offline Mode.
    When switching to offline_mode, sets offline_lease_until for the specified days.
    """
    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    lease_until = (now_dt + timedelta(days=lease_days)).strftime("%Y-%m-%d %H:%M:%S") if mode == "offline_mode" else None

    cursor = conn.cursor()
    cursor.execute("SELECT id, smart_ease_account_id, full_name FROM users WHERE id = ? OR smart_ease_account_id = ?", (user_id, user_id))
    user = cursor.fetchone()
    if not user:
        if should_close:
            conn.close()
        return {"status": "error", "message": "User not found"}

    with conn:
        conn.execute("""
            UPDATE users
            SET connection_mode = ?, offline_lease_until = ?, updated_at = ?
            WHERE id = ?
        """, (mode, lease_until, now_iso, user["id"]))

        conn.execute("""
            INSERT INTO user_presence_logs (
                user_id, account_id, event_type, connection_mode, source_app, created_at
            ) VALUES (?, ?, 'mode_switch', ?, 'AdminConsole', ?)
        """, (user["id"], user["smart_ease_account_id"], mode, now_iso))

    sync_sqlite_to_mongo(conn)

    if should_close:
        conn.close()

    return {
        "status": "success",
        "user_id": user["id"],
        "account_id": user["smart_ease_account_id"],
        "connection_mode": mode,
        "offline_lease_until": lease_until
    }

def get_presence_metrics(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """
    Calculates dynamic presence counts:
    - online_now (heartbeat within 10 minutes)
    - offline_count (app closed or last_seen > 10 mins or never)
    - uses_offline_mode (specifically configured/licensed for offline field work)
    - uses_online_mode (configured for online sync)
    - recent_active_today (heartbeat within 24 hours)
    """
    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    now_dt = datetime.now(timezone.utc)
    cutoff_online = (now_dt - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_recent = (now_dt - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM users")
    total_users = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM users WHERE last_seen IS NOT NULL AND last_seen >= ?", (cutoff_online,))
    online_now = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM users WHERE connection_mode = 'offline_mode'")
    uses_offline_mode = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM users WHERE connection_mode = 'online'")
    uses_online_mode = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM users WHERE last_seen IS NULL OR last_seen < ?", (cutoff_online,))
    offline_count = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) as cnt FROM users WHERE last_seen IS NOT NULL AND last_seen >= ?", (cutoff_recent,))
    recent_active_today = cursor.fetchone()["cnt"]

    if should_close:
        conn.close()

    return {
        "total_users": total_users,
        "online_now": online_now,
        "offline_count": offline_count,
        "uses_offline_mode": uses_offline_mode,
        "uses_online_mode": uses_online_mode,
        "recent_active_today": recent_active_today
    }



# ==========================================
# 7. USER LOGINS & SECURITY ACTIVITY LOGGING
# ==========================================

def ensure_user_logins_table(conn: sqlite3.Connection):
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_logins (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                email TEXT NOT NULL,
                full_name TEXT,
                product TEXT NOT NULL DEFAULT 'Smart Ease Photobook Designer',
                device_hostname TEXT,
                ip_address TEXT,
                auth_method TEXT NOT NULL DEFAULT 'email',
                login_time TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'SUCCESS',
                alert_dispatched INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logins_email ON user_logins(email);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logins_time ON user_logins(login_time);")
    except Exception:
        pass


def record_user_login(
    email: str,
    full_name: Optional[str] = None,
    product: str = "Smart Ease Photobook Designer",
    device_hostname: Optional[str] = None,
    auth_method: str = "email",
    ip_address: Optional[str] = None,
    user_id: Optional[str] = None,
    status: str = "SUCCESS",
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    import uuid
    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    ensure_user_logins_table(conn)

    login_id = f"LGN-{uuid.uuid4().hex[:8].upper()}"
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    cursor = conn.cursor()
    if not user_id:
        cursor.execute("SELECT id, full_name FROM users WHERE LOWER(email) = ?", (email.lower(),))
        row = cursor.fetchone()
        if row:
            user_id = row["id"]
            if not full_name:
                full_name = row["full_name"]

    if not full_name:
        full_name = email.split("@")[0].capitalize()

    # Only claim the user was alerted if an email can actually be sent.
    from email_service import is_smtp_configured
    alerted = 1 if is_smtp_configured() else 0

    conn.execute("""
        INSERT INTO user_logins (
            id, user_id, email, full_name, product, device_hostname,
            ip_address, auth_method, login_time, status, alert_dispatched, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        login_id, user_id, email.lower(), full_name, product,
        device_hostname or "Desktop", ip_address or "127.0.0.1",
        auth_method, now_iso, status, alerted, now_iso
    ))

    if user_id:
        try:
            cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,))
            if cursor.fetchone():
                conn.execute("UPDATE users SET last_seen = ?, updated_at = ? WHERE id = ?", (now_iso, now_iso, user_id))
                conn.execute("""
                    INSERT INTO user_presence_logs (user_id, account_id, event_type, connection_mode, source_app, device_hostname, ip_address, created_at)
                    VALUES (?, ?, 'login', 'online', ?, ?, ?, ?)
                """, (user_id, user_id, product, device_hostname or "Desktop", ip_address or "127.0.0.1", now_iso))
        except Exception as pe:
            print(f"[database] Note on presence log update: {pe}")

    conn.commit()

    # Mirror to MongoDB cloud if configured
    try:
        from mongo_db import get_mongo_db
        db = get_mongo_db()
        if db is not None:
            doc = {
                "_id": login_id,
                "id": login_id,
                "user_id": user_id,
                "email": email.lower(),
                "full_name": full_name,
                "product": product,
                "device_hostname": device_hostname or "Desktop",
                "ip_address": ip_address or "127.0.0.1",
                "auth_method": auth_method,
                "login_time": now_iso,
                "status": status,
                "alert_dispatched": alerted,
                "created_at": now_iso
            }
            db["user_logins"].replace_one({"_id": login_id}, doc, upsert=True)
            if user_id:
                db["users"].update_one({"_id": user_id}, {"$set": {"last_seen": now_iso, "updated_at": now_iso}})
    except Exception as me:
        print(f"[MongoLogins] Note: {me}")

    record = {
        "id": login_id,
        "user_id": user_id,
        "email": email.lower(),
        "full_name": full_name,
        "product": product,
        "device_hostname": device_hostname or "Desktop",
        "ip_address": ip_address or "127.0.0.1",
        "auth_method": auth_method,
        "login_time": now_iso,
        "status": status,
        "alert_dispatched": bool(alerted)
    }

    if should_close:
        conn.close()

    return record

def get_recent_logins(limit: int = 30, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    ensure_user_logins_table(conn)

    try:
        from mongo_db import get_mongo_db
        db = get_mongo_db()
        if db is not None:
            cursor_m = db["user_logins"].find({}).sort("login_time", -1).limit(limit)
            docs = list(cursor_m)
            if docs:
                res = []
                for d in docs:
                    d_copy = dict(d)
                    d_copy.pop("_id", None)
                    res.append(d_copy)
                if should_close:
                    conn.close()
                return res
    except Exception:
        pass

    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM user_logins
        ORDER BY login_time DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    results = [dict(r) for r in rows]

    if should_close:
        conn.close()

    return results

def get_user_logins(user_id_or_email: str, limit: int = 20, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    should_close = False
    if conn is None:
        conn = get_db_connection()
        should_close = True

    ensure_user_logins_table(conn)

    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM user_logins
        WHERE user_id = ? OR LOWER(email) = ?
        ORDER BY login_time DESC
        LIMIT ?
    """, (user_id_or_email, user_id_or_email.lower(), limit))
    rows = cursor.fetchall()
    results = [dict(r) for r in rows]

    if should_close:
        conn.close()

    return results
