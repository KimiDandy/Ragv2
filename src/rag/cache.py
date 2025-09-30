import os
import sqlite3
import json
import time
import hashlib
from threading import Lock

BASE_DIR = os.path.join(os.getcwd(), "cache")
DB_PATH = os.path.join(BASE_DIR, "local_cache.sqlite")
os.makedirs(BASE_DIR, exist_ok=True)

_con = sqlite3.connect(DB_PATH, check_same_thread=False)
_con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, ts REAL)")
_con.commit()
_lock = Lock()

def key_for(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()

def get(k: str):
    with _lock:
        cur = _con.execute("SELECT v FROM kv WHERE k=?", (k,))
        row = cur.fetchone()
    return json.loads(row[0]) if row and row[0] else None

def set(k: str, obj: dict):
    with _lock:
        _con.execute(
            "REPLACE INTO kv(k,v,ts) VALUES(?,?,?)",
            (k, json.dumps(obj, ensure_ascii=False), time.time()),
        )
        _con.commit()
