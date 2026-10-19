"""
Smart Ease Subscription Manager - MongoDB Persistence & Synchronization Engine
Provides indestructible cloud persistence when running on Render or any containerized host.
Mirrors SQLite data to/from MongoDB collections:
  - packages
  - users
  - subscriptions
  - transactions
"""

import os
import sqlite3
from typing import Optional, Dict, Any, List

try:
    import pymongo
    from pymongo import MongoClient, ReplaceOne
except ImportError:
    pymongo = None
    MongoClient = None
    ReplaceOne = None

_client: Optional[Any] = None

def get_mongo_uri() -> Optional[str]:
    return os.environ.get("MONGODB_URI") or os.environ.get("MONGO_URI") or os.environ.get("DATABASE_URL")

def get_mongo_client():
    global _client
    if MongoClient is None:
        return None
    uri = get_mongo_uri()
    if not uri or not uri.startswith(("mongodb://", "mongodb+srv://")):
        return None
    if _client is None:
        try:
            _client = MongoClient(
                uri,
                serverSelectionTimeoutMS=4000,
                connectTimeoutMS=4000,
                socketTimeoutMS=4000
            )
        except Exception as e:
            print(f"[MongoDB] Connection error: {e}. Please ensure 0.0.0.0/0 is whitelisted in MongoDB Atlas Network Access.")
            return None
    return _client

def get_mongo_db():
    client = get_mongo_client()
    if not client:
        return None
    db_name = os.environ.get("MONGODB_DB_NAME", "smart_ease_subscriptions")
    return client[db_name]

def is_mongo_connected() -> Dict[str, Any]:
    uri = get_mongo_uri()
    if not uri:
        return {"configured": False, "connected": False, "status": "No MONGODB_URI set (using local SQLite)"}
    if MongoClient is None:
        return {"configured": True, "connected": False, "status": "pymongo not installed"}
    client = get_mongo_client()
    if not client:
        return {"configured": True, "connected": False, "status": "Failed to initialize client"}
    try:
        client.admin.command("ping")
        db = get_mongo_db()
        return {
            "configured": True,
            "connected": True,
            "status": "Connected to MongoDB",
            "database": db.name if db else "unknown"
        }
    except Exception as e:
        return {"configured": True, "connected": False, "status": f"Connection failed: {e}"}

def sync_sqlite_to_mongo(conn: sqlite3.Connection):
    """Upsert all SQLite records into MongoDB."""
    db = get_mongo_db()
    if db is None or ReplaceOne is None:
        return
    try:
        tables = ["packages", "users", "subscriptions", "transactions"]
        for table in tables:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM {table}")
            rows = cursor.fetchall()
            if not rows:
                continue
            
            ops = []
            for row in rows:
                doc = dict(row)
                row_id = doc.get("id")
                if not row_id:
                    continue
                doc["_id"] = row_id
                ops.append(ReplaceOne({"_id": row_id}, doc, upsert=True))
            
            if ops:
                db[table].bulk_write(ops, ordered=False)
        print("[MongoDB] Successfully synchronized local state to MongoDB cloud.")
    except Exception as e:
        print(f"[MongoDB] sync_sqlite_to_mongo error: {e}")

def sync_mongo_to_sqlite(conn: sqlite3.Connection):
    """Restore MongoDB cloud documents into SQLite on server startup."""
    db = get_mongo_db()
    if db is None:
        return
    try:
        # Check if MongoDB has data
        packages_cnt = db["packages"].count_documents({})
        users_cnt = db["users"].count_documents({})
        if packages_cnt == 0 and users_cnt == 0:
            # MongoDB is empty, sync initial SQLite seed up to MongoDB!
            sync_sqlite_to_mongo(conn)
            return

        tables = ["packages", "users", "subscriptions", "transactions"]
        for table in tables:
            docs = list(db[table].find({}))
            if not docs:
                continue
            with conn:
                for doc in docs:
                    # remove MongoDB internal _id for SQLite insert
                    doc_copy = dict(doc)
                    pk_id = doc_copy.pop("_id", None)
                    if not pk_id and "id" in doc_copy:
                        pk_id = doc_copy["id"]
                    if not pk_id:
                        continue
                    
                    columns = list(doc_copy.keys())
                    placeholders = ", ".join(["?"] * len(columns))
                    col_names = ", ".join(columns)
                    vals = [doc_copy[c] for c in columns]
                    
                    sql = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"
                    conn.execute(sql, vals)
        print("[MongoDB] Successfully restored and synchronized data from MongoDB cloud into SQLite.")
    except Exception as e:
        print(f"[MongoDB] sync_mongo_to_sqlite error: {e}")
