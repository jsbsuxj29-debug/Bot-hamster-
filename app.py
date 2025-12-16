#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# complete_income_vpn_bot_final.py
# Full Flask app (VIP + Ads). Save this file and run with: python3 complete_income_vpn_bot_final.py
# Requirements: pip install flask flask-socketio eventlet
# Optional: pip install gTTS for TTS support

import os
import sys
import time
import sqlite3
import secrets
import logging
import csv
import io
import json
from pathlib import Path
from functools import wraps
from flask import (
    Flask, request as flask_request, jsonify, redirect,
    url_for, render_template_string, session, make_response, Response, send_from_directory
)
from flask_socketio import SocketIO, join_room, emit

# Optional TTS
try:
    from gtts import gTTS
    TTS_AVAILABLE = True
except Exception:
    TTS_AVAILABLE = False

# ---------- Configuration ----------
APP_HOST = os.environ.get("APP_HOST", "0.0.0.0")
APP_PORT = int(os.environ.get("APP_PORT", "5000"))
DB_PATH = os.environ.get("DB_PATH", "data_app.db")
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "uploads")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin123")
APP_SECRET = os.environ.get("APP_SECRET", secrets.token_urlsafe(32))
REYMIT_BASE = os.environ.get("REYMIT_BASE", "https://reymit.ir/kakoo")
REYMIT_SECRET = os.environ.get("REYMIT_SECRET", "")

# Business rules / limits
POINTS_PER_CLIP = int(os.environ.get("POINTS_PER_CLIP", "30"))
POINTS_PER_LINK = int(os.environ.get("POINTS_PER_LINK", "15"))
POINTS_PER_JOIN = int(os.environ.get("POINTS_PER_JOIN", "40"))
MIN_CLIP_INTERVAL = int(os.environ.get("MIN_CLIP_INTERVAL", "20"))
MIN_LINK_INTERVAL = int(os.environ.get("MIN_LINK_INTERVAL", "15"))
MIN_JOIN_INTERVAL = int(os.environ.get("MIN_JOIN_INTERVAL", "60"))
MIN_WITHDRAW_POINTS = int(os.environ.get("MIN_WITHDRAW_POINTS", "100"))
AFFILIATE_SHARE = float(os.environ.get("AFFILIATE_SHARE", "0.30"))
ONLINE_SECONDS = int(os.environ.get("ONLINE_SECONDS", "300"))
MAX_UPLOAD_SIZE = 6 * 1024 * 1024  # 6 MB

# VIP pack definitions (prices in Toman, days, vip_multiplier)
VIP_PACKS = {
    "week": {"label": "یک هفته", "price": 85000, "days": 7, "multiplier": 1.10},
    "month": {"label": "یک ماه", "price": 245000, "days": 30, "multiplier": 1.25},
    "3month": {"label": "سه ماه", "price": 670000, "days": 90, "multiplier": 1.5}
}

# AD promo packs (5) and join packs (3)
AD_PROMO_PACKS = {
    "p1": {"label": "پکیج تبلیغاتی 1", "price": 50000, "slots": 1},
    "p2": {"label": "پکیج تبلیغاتی 2", "price": 120000, "slots": 3},
    "p3": {"label": "پکیج تبلیغاتی 3", "price": 250000, "slots": 8},
    "p4": {"label": "پکیج تبلیغاتی 4", "price": 450000, "slots": 20},
    "p5": {"label": "پکیج تبلیغاتی 5", "price": 1000000, "slots": 50}
}
AD_JOIN_PACKS = {
    "j1": {"label": "عضویت پایه", "price": 15000, "days": 7},
    "j2": {"label": "عضویت استاندارد", "price": 40000, "days": 30},
    "j3": {"label": "عضویت ویژه", "price": 100000, "days": 90}
}

# Points conversion: 1 point = TOMAN_PER_POINT
TOMAN_PER_POINT = int(os.environ.get("TOMAN_PER_POINT", "100"))

# ---------- Logging ----------
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("income-vpn-bot")

# ---------- Flask + SocketIO ----------
app = Flask(__name__)
app.secret_key = APP_SECRET
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE
Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)
Path(os.path.join(UPLOAD_FOLDER, "tts")).mkdir(parents=True, exist_ok=True)
socketio = SocketIO(app, cors_allowed_origins="*")

# ---------- Database init and migrations ----------
def _get_conn():
    return sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)

def ensure_tables():
    conn = _get_conn(); c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        points INTEGER DEFAULT 0,
        card TEXT DEFAULT NULL,
        phone TEXT DEFAULT NULL,
        auth_token TEXT DEFAULT NULL,
        last_clip_ts INTEGER DEFAULT 0,
        last_link_ts INTEGER DEFAULT 0,
        last_join_ts INTEGER DEFAULT 0,
        referrer INTEGER DEFAULT NULL,
        last_active INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS earning_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        kind TEXT,
        points INTEGER,
        amount_toman INTEGER,
        created_at INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS withdraws (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        points INTEGER,
        amount_toman INTEGER,
        card TEXT,
        phone TEXT,
        status TEXT DEFAULT 'pending',
        created_at INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        screenshot TEXT,
        payment_ref TEXT DEFAULT NULL,
        type TEXT DEFAULT 'vpn',
        subtype TEXT DEFAULT NULL,
        status TEXT DEFAULT 'pending',
        admin_note TEXT DEFAULT NULL,
        vpn_user TEXT DEFAULT NULL,
        vpn_pass TEXT DEFAULT NULL,
        vpn_expires INTEGER DEFAULT NULL,
        created_at INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS config (
        k TEXT PRIMARY KEY,
        v TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS ads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        purchase_id INTEGER,
        user_id INTEGER,
        title TEXT,
        channel_link TEXT,
        description TEXT,
        image TEXT,
        phone TEXT,
        status TEXT DEFAULT 'active',
        created_at INTEGER,
        approved_at INTEGER
    )""")
    # ad_events table to track impressions & clicks
    c.execute("""CREATE TABLE IF NOT EXISTS ad_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ad_id INTEGER,
        user_id INTEGER,
        kind TEXT,
        created_at INTEGER
    )""")
    # promo codes table
    c.execute("""CREATE TABLE IF NOT EXISTS promo_codes (
        code TEXT PRIMARY KEY,
        points INTEGER,
        created_by INTEGER,
        used_by INTEGER DEFAULT NULL,
        used_at INTEGER DEFAULT NULL,
        expires_at INTEGER DEFAULT NULL,
        created_at INTEGER
    )""")
    conn.commit()
    # add vip columns to users if missing
    c.execute("PRAGMA table_info(users)")
    cols = [r[1] for r in c.fetchall()]
    if 'vip_expires' not in cols:
        try: c.execute("ALTER TABLE users ADD COLUMN vip_expires INTEGER DEFAULT 0")
        except: pass
    if 'vip_tier' not in cols:
        try: c.execute("ALTER TABLE users ADD COLUMN vip_tier TEXT DEFAULT NULL")
        except: pass
    if 'vip_multiplier' not in cols:
        try: c.execute("ALTER TABLE users ADD COLUMN vip_multiplier REAL DEFAULT 1.0")
        except: pass
    conn.commit(); conn.close()

ensure_tables()

# ---------- Config helpers (persist admin settings) ----------
def set_config(key, value):
    conn = _get_conn(); c = conn.cursor()
    v = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    c.execute("INSERT OR REPLACE INTO config (k, v) VALUES (?, ?)", (key, v))
    conn.commit(); conn.close()

def get_config(key, default=None):
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT v FROM config WHERE k=?", (key,))
    r = c.fetchone(); conn.close()
    if not r:
        return default
    try:
        return json.loads(r[0])
    except Exception:
        return r[0]

def load_persisted_settings():
    global POINTS_PER_CLIP, POINTS_PER_LINK, POINTS_PER_JOIN, MIN_WITHDRAW_POINTS
    global AFFILIATE_SHARE, ONLINE_SECONDS, TOMAN_PER_POINT, BIG_ACTION_LABEL, REYMIT_BASE
    global VIP_PACKS, AD_PROMO_PACKS, AD_JOIN_PACKS
    p = get_config("POINTS_PER_CLIP"); 
    if p is not None: POINTS_PER_CLIP = int(p)
    p = get_config("POINTS_PER_LINK"); 
    if p is not None: POINTS_PER_LINK = int(p)
    p = get_config("POINTS_PER_JOIN"); 
    if p is not None: POINTS_PER_JOIN = int(p)
    p = get_config("MIN_WITHDRAW_POINTS"); 
    if p is not None: MIN_WITHDRAW_POINTS = int(p)
    p = get_config("AFFILIATE_SHARE"); 
    if p is not None: AFFILIATE_SHARE = float(p)
    p = get_config("ONLINE_SECONDS"); 
    if p is not None: ONLINE_SECONDS = int(p)
    p = get_config("TOMAN_PER_POINT")
    if p is not None: TOMAN_PER_POINT = int(p)
    p = get_config("BIG_ACTION_LABEL")
    if p is not None: BIG_ACTION_LABEL = str(p)
    p = get_config("VIP_PACKS")
    if p is not None:
        try: VIP_PACKS = p
        except: pass
    p = get_config("AD_PROMO_PACKS")
    if p is not None:
        try: AD_PROMO_PACKS = p
        except: pass
    p = get_config("AD_JOIN_PACKS")
    if p is not None:
        try: AD_JOIN_PACKS = p
        except: pass
    p = get_config("REYMIT_BASE")
    if p is not None:
        REYMIT_BASE = str(p)

load_persisted_settings()

# ---------- Core helpers ----------
def get_user(uid):
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT user_id, points, card, phone, auth_token, last_clip_ts, last_link_ts, last_join_ts, referrer, last_active, vip_expires, vip_tier, vip_multiplier FROM users WHERE user_id=?", (uid,))
    r = c.fetchone(); conn.close(); return r

def create_user(uid, referrer=None):
    now = int(time.time())
    conn = _get_conn(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, last_active, referrer) VALUES (?,?,?)", (uid, now, referrer))
    if referrer:
        c.execute("SELECT referrer FROM users WHERE user_id=?", (uid,))
        row = c.fetchone()
        if row and row[0] is None and referrer != uid:
            c.execute("UPDATE users SET referrer=? WHERE user_id=?", (referrer, uid))
    conn.commit(); conn.close()

def ensure_token(uid):
    u = get_user(uid)
    if not u:
        create_user(uid); u = get_user(uid)
    token = u[4]
    if token:
        update_last_active(uid); return token
    token = secrets.token_urlsafe(18)
    conn = _get_conn(); c = conn.cursor()
    c.execute("UPDATE users SET auth_token=?, last_active=? WHERE user_id=?", (token, int(time.time()), uid))
    conn.commit(); conn.close()
    return token

def update_last_active(uid):
    conn = _get_conn(); c = conn.cursor()
    c.execute("UPDATE users SET last_active=? WHERE user_id=?", (int(time.time()), uid))
    conn.commit(); conn.close()

def change_points(uid, delta):
    conn = _get_conn(); c = conn.cursor()
    c.execute("UPDATE users SET points = points + ? WHERE user_id=?", (delta, uid))
    conn.commit(); conn.close()

def set_last_ts(uid, field, ts):
    if field not in ("last_clip_ts", "last_link_ts", "last_join_ts"):
        raise ValueError("invalid ts field")
    conn = _get_conn(); c = conn.cursor()
    c.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (ts, uid))
    conn.commit(); conn.close()

def log_earning_event(uid, kind, points):
    amount = points * TOMAN_PER_POINT
    conn = _get_conn(); c = conn.cursor(); ts = int(time.time())
    c.execute("INSERT INTO earning_events (user_id, kind, points, amount_toman, created_at) VALUES (?,?,?,?,?)", (uid, kind, points, amount, ts))
    conn.commit(); conn.close(); return amount

def create_withdraw(uid, points, amount_toman, card, phone):
    conn = _get_conn(); c = conn.cursor(); ts = int(time.time())
    c.execute("INSERT INTO withdraws (user_id, points, amount_toman, card, phone, status, created_at) VALUES (?,?,?,?,?,?,?)", (uid, points, amount_toman, card, phone, "pending", ts))
    conn.commit(); wid = c.lastrowid; conn.close(); return wid

def create_purchase(uid, amount, screenshot_filename, payment_ref=None, ptype='vpn', subtype=None):
    conn = _get_conn(); c = conn.cursor(); ts = int(time.time())
    c.execute("INSERT INTO purchases (user_id, amount, screenshot, payment_ref, type, subtype, status, created_at) VALUES (?,?,?,?,?,?,?,?)", (uid, amount, screenshot_filename, payment_ref or None, ptype, subtype, "pending", ts))
    conn.commit(); pid = c.lastrowid; conn.close(); return pid

def list_purchases(status=None):
    conn = _get_conn(); c = conn.cursor()
    if status:
        c.execute("SELECT id,user_id,amount,screenshot,payment_ref,type,subtype,status,admin_note,vpn_user,vpn_pass,vpn_expires,created_at FROM purchases WHERE status=? ORDER BY created_at DESC", (status,))
    else:
        c.execute("SELECT id,user_id,amount,screenshot,payment_ref,type,subtype,status,admin_note,vpn_user,vpn_pass,vpn_expires,created_at FROM purchases ORDER BY created_at DESC")
    rows = c.fetchall(); conn.close(); return rows

# Robust get_user_purchases: logs on error and returns empty list instead of crashing.
def get_user_purchases(uid, types=None):
    conn = _get_conn()
    try:
        c = conn.cursor()
        if types:
            placeholders = ",".join("?" * len(types))
            query = (
                "SELECT id,user_id,amount,screenshot,payment_ref,type,subtype,status,created_at "
                f"FROM purchases WHERE user_id=? AND type IN ({placeholders}) ORDER BY created_at DESC"
            )
            params = [uid] + list(types)
            c.execute(query, params)
        else:
            c.execute(
                "SELECT id,user_id,amount,screenshot,payment_ref,type,subtype,status,created_at "
                "FROM purchases WHERE user_id=? ORDER BY created_at DESC",
                (uid,),
            )
        rows = c.fetchall()
        return rows
    except Exception:
        log.exception("get_user_purchases failed uid=%s types=%s", uid, types)
        return []
    finally:
        conn.close()

def update_purchase_status(purchase_id, status, admin_note=None, vpn_user=None, vpn_pass=None, vpn_expires=None):
    conn = _get_conn(); c = conn.cursor()
    c.execute("UPDATE purchases SET status=?, admin_note=?, vpn_user=?, vpn_pass=?, vpn_expires=? WHERE id=?", (status, admin_note, vpn_user, vpn_pass, vpn_expires, purchase_id))
    conn.commit(); conn.close()

def list_withdraws(status=None):
    conn = _get_conn(); c = conn.cursor()
    if status:
        c.execute("SELECT id,user_id,points,amount_toman,card,phone,status,created_at FROM withdraws WHERE status=? ORDER BY created_at DESC", (status,))
    else:
        c.execute("SELECT id,user_id,points,amount_toman,card,phone,status,created_at FROM withdraws ORDER BY created_at DESC")
    rows = c.fetchall(); conn.close(); return rows

def update_withdraw_status(wid, status):
    conn = _get_conn(); c = conn.cursor()
    c.execute("UPDATE withdraws SET status=? WHERE id=?", (status, wid)); conn.commit(); conn.close()

def get_subscribers_of(uid):
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT user_id, last_active FROM users WHERE referrer=?", (uid,))
    rows = c.fetchall(); conn.close(); return rows

def get_affiliate_earnings(uid):
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT SUM(points), SUM(amount_toman) FROM earning_events WHERE user_id=? AND kind LIKE 'affiliate_%'", (uid,))
    r = c.fetchone(); conn.close(); pts = r[0] or 0; tomans = r[1] or 0
    return {"points": pts, "amount_toman": tomans}

def get_total_earnings_toman(uid):
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT SUM(amount_toman) FROM earning_events WHERE user_id=?", (uid,))
    r = c.fetchone(); conn.close(); return r[0] or 0

# ---------- Helpers: VIP & affiliate multipliers ----------
def user_vip_info(uid):
    u = get_user(uid)
    if not u:
        return {"is_vip": False, "days_left": 0, "tier": None, "multiplier": 1.0}
    vip_expires = u[10] or 0
    vip_tier = u[11]
    vip_multiplier = u[12] or 1.0
    now = int(time.time())
    days_left = max(0, (vip_expires - now) // (24*3600)) if vip_expires and vip_expires > now else 0
    return {"is_vip": bool(vip_expires and vip_expires > now), "days_left": days_left, "tier": vip_tier, "multiplier": vip_multiplier}

def apply_vip_multiplier_for_points(uid, base_points):
    u = get_user(uid)
    if not u:
        return base_points
    vip_mult = u[12] or 1.0
    awarded = int(round(base_points * vip_mult))
    if awarded <= 0:
        awarded = base_points
    return awarded

def get_referrer_multiplier(referrer_id):
    r = get_user(referrer_id)
    if not r:
        return 1.0
    return r[12] or 1.0

# ---------- Admin decorator ----------
def admin_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrapped

# ---------- Serve uploads (for admin and ads) ----------
@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=False)

@app.route("/uploads/tts/<path:filename>")
def uploaded_tts(filename):
    return send_from_directory(os.path.join(UPLOAD_FOLDER, "tts"), filename, as_attachment=False)

# ---------- Static: manifest & service worker ----------
MANIFEST = {
  "name": "ربات کسب درآمد تومانی",
  "short_name": "ربات درآمد",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#041426",
  "theme_color": "#00d4ff",
  "icons": [
    {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"}
  ]
}
SW_JS = """
self.addEventListener('install', function(e){ self.skipWaiting(); });
self.addEventListener('activate', function(e){ clients.claim(); });
self.addEventListener('fetch', function(event){});
"""

@app.route("/manifest.json")
def manifest_json():
    return jsonify(MANIFEST)

@app.route("/sw.js")
def sw_js():
    resp = make_response(SW_JS)
    resp.headers['Content-Type'] = 'application/javascript'
    return resp

# ---------- Mobile-first base CSS and client JS ----------
# Use a regular (non-f) string so embedded braces and Jinja {{ }} are not evaluated by Python.
BASE_STYLES = """
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<style>
:root{
  --bg:#041426; --card:#0b2436; --accent1:#00d4ff; --accent2:#6af0c2; --muted:#9fbcd0;
  --text:#e6f3f8; --primary-text:#04283a;
}
html { -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; font-family: "Vazirmatn", Tahoma, Arial, sans-serif; font-size:16px; }
body { margin:0; background:var(--bg); color:var(--text); padding:14px; direction:rtl; line-height:1.45; }
.container { max-width:900px; margin:0 auto; padding-bottom:40px; }
.card { background:var(--card); padding:14px; border-radius:12px; border:1px solid rgba(255,255,255,0.03); margin-bottom:12px; box-shadow: 0 2px 8px rgba(0,0,0,0.25); }
h2 { margin:0 0 8px 0; font-size:18px; }
.small { color:var(--muted); font-size:14px; }
.badge{display:inline-block;background:#062b3a;padding:6px 10px;border-radius:999px;color:var(--muted);font-weight:700;font-size:13px}
.table{width:100%;border-collapse:collapse;margin-top:8px}
.table th,.table td{padding:10px 8px;border-bottom:1px solid rgba(255,255,255,0.03);text-align:right;font-size:14px}
.grid{display:grid;grid-template-columns:1fr;gap:12px;margin-top:12px}
@media(min-width:720px){ .grid{grid-template-columns:repeat(2,1fr);} }
@media(min-width:1100px){ .grid{grid-template-columns:2fr 1fr;} }
.btn{
  display:inline-flex;gap:8px;align-items:center;justify-content:center;
  background:linear-gradient(90deg,var(--accent1),var(--accent2));border:none;
  color:var(--primary-text);padding:14px 16px;border-radius:12px;font-weight:800;cursor:pointer;
  font-size:16px; height:auto; min-height:50px; width:100%;
  box-shadow: 0 6px 18px rgba(0,0,0,0.35);
}
.btn.secondary{background:transparent;border:1px solid rgba(255,255,255,0.06);color:var(--muted);font-weight:700}
.big-action{display:block;padding:16px;border-radius:14px;background:linear-gradient(90deg,#ff9a00,#ff3b30);color:var(--primary-text);text-align:center;font-weight:900;font-size:18px;margin-top:12px;box-shadow:0 8px 20px rgba(0,0,0,0.35)}
.pack-card{background:linear-gradient(180deg,rgba(255,255,255,0.02),transparent);padding:12px;border-radius:10px;text-align:right}
.input, input[type="text"], input[type="number"], input[type="file"], textarea, select {
  width:100%; padding:12px; border-radius:10px; border:1px solid rgba(255,255,255,0.04); background:rgba(255,255,255,0.02); color:var(--text); font-size:15px;
}
textarea { min-height:110px; }
.ref { width:100%; padding:10px; border-radius:10px; background:transparent; border:1px dashed rgba(255,255,255,0.04); color:var(--text); }
.toast{position:fixed;left:12px;right:12px;bottom:18px;background:rgba(0,0,0,0.7);padding:12px 14px;border-radius:10px;color:#fff;z-index:9999;box-shadow:0 8px 24px rgba(0,0,0,0.6)}
.help-overlay{position:fixed;top:8%;left:6%;right:6%;background:var(--card);padding:18px;border-radius:12px;border:1px solid rgba(255,255,255,0.04);z-index:9998}
.icon-circle{width:52px;height:52px;border-radius:12px;display:inline-flex;align-items:center;justify-content:center;font-weight:900;color:var(--primary-text);background:linear-gradient(90deg,var(--accent1),var(--accent2));}
.table-responsive{overflow:auto;border-radius:8px}
.footer-note{font-size:13px;color:var(--muted);margin-top:8px}
</style>

<script src="/socket.io/socket.io.js"></script>
<script>
const socket = io();
socket.on('connect', ()=>{ console.log('socket connected'); });
function joinUidRoom(uid){ if(!uid) return; socket.emit('join', {uid: uid}); }
socket.on('purchase_approved', function(d){ showToast('✅ سفارش شما تایید شد'); fetchBalance(d.uid); });
socket.on('purchase_rejected', function(d){ showToast('❌ سفارش شما رد شد'); });
socket.on('withdraw_approved', function(d){ showToast('✅ برداشت شما تایید شد'); fetchBalance(d.uid); });
socket.on('withdraw_rejected', function(d){ showToast('❌ برداشت شما رد شد'); });
function showToast(msg, timeout=4000){ const t = document.createElement('div'); t.className='toast'; t.textContent=msg; document.body.appendChild(t); setTimeout(()=>{ t.style.opacity=1; },50); setTimeout(()=>{ t.remove(); }, timeout); }
function fetchBalance(uid){ if(!uid) return; fetch('/balance-json?uid=' + encodeURIComponent(uid)).then(r=>r.json()).then(d=>{ if(!d.ok) return; const pts = d.points||0; const tom = pts * __TOMAN__; const elPts = document.getElementById('ui-points'); const elTom = document.getElementById('ui-toman'); const elStatus = document.getElementById('ui-status'); const elAff = document.getElementById('ui-aff'); if(elPts) elPts.textContent = pts + ' امتیاز'; if(elTom) elTom.textContent = tom + ' تومان'; if(elStatus) elStatus.textContent = (d.online ? '✅ آنلاین' : '⚪️ آفلاین'); if(elAff) elAff.textContent = 'زیرمجموعه: ' + (d.affiliate_points||0) + ' امتیاز — ' + (d.affiliate_toman||0) + ' تومان'; }).catch(e=>console.warn(e)); }
document.addEventListener('keydown', function(e){ if((e.target.tagName || '').match(/INPUT|TEXTAREA/)) return; if(e.key === ' '){ e.preventDefault(); const uid = getUid(); if(uid) location.href='/page/ads/clip?uid='+uid; } if(e.key.toLowerCase() === 'p'){ const uid = getUid(); if(uid) location.href='/page/profile?uid='+uid; } if(e.key.toLowerCase() === 'r'){ const uid = getUid(); if(uid) location.href='/page/referral?uid='+uid; } if(e.key.toLowerCase() === 'v'){ const uid = getUid(); if(uid) location.href='/page/vip?uid='+uid; } if(e.key === '?'){ toggleHelp(); } });
function toggleHelp(){ const h = document.getElementById('help-overlay'); if(!h) return; h.classList.toggle('hidden'); }
function getUid(){ return (new URLSearchParams(location.search)).get('uid') || '1'; }
function setTheme(mode){ if(mode==='dark'){ document.documentElement.style.setProperty('--bg','#041426'); document.documentElement.style.setProperty('--card','#0b2436'); } else { document.documentElement.style.setProperty('--bg','#f7f9fc'); document.documentElement.style.setProperty('--card','#ffffff'); } localStorage.setItem('theme',mode); }
(function(){ const t = localStorage.getItem('theme') || 'dark'; setTheme(t); })();
</script>
"""
# inject the numeric value safely
BASE_STYLES = BASE_STYLES.replace("__TOMAN__", str(TOMAN_PER_POINT))

# ---------- Full templates (mobile-first) ----------
HOME_TEMPLATE = """
<!doctype html><html lang="fa"><head><meta charset="utf-8"><title>ربات کسب درآمد</title>""" + BASE_STYLES + """</head><body>
<div class="container">
  <div class="card" style="display:flex;justify-content:space-between;align-items:center;gap:12px">
    <div style="display:flex;gap:12px;align-items:center">
      <div class="icon-circle">ت</div>
      <div>
        <div style="font-weight:900">ربات کسب درآمد تومانی</div>
        <div class="small">جامع — امن — حرفه‌ای</div>
      </div>
    </div>
    <div style="display:flex;flex-direction:column;align-items:flex-end;gap:8px">
      <div class="badge" id="ui-status">—</div>
      <div style="display:flex;gap:8px">
        <button class="btn secondary" onclick="setTheme(localStorage.getItem('theme')==='dark'?'light':'dark')">🌓 تم</button>
        <button class="btn secondary" onclick="location.href='/admin/login'">🔧 پنل</button>
      </div>
    </div>
  </div>

  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <div>شناسه شما (UID): <strong id="uid-show">{{ uid }}</strong></div>
      <div class="small">امتیاز: <strong id="ui-points">{{ points }}</strong></div>
    </div>
    <div style="margin-top:12px" class="grid">
      <button class="btn" onclick="location.href='/page/ads/clip?uid={{ uid }}'">🎬 کلیپ — +{{ clip }}</button>
      <button class="btn" onclick="location.href='/page/ads/link?uid={{ uid }}'">🔗 لینک — +{{ link }}</button>
      <button class="btn" onclick="location.href='/page/ads/join?uid={{ uid }}'">👥 عضویت — +{{ join }}</button>
      <button class="btn" onclick="location.href='/page/referral?uid={{ uid }}'">🤝 زیرمجموعه</button>
      <button class="btn" onclick="location.href='/page/profile?uid={{ uid }}'">🧾 پروفایل</button>
      <button class="btn" onclick="location.href='/page/vip?uid={{ uid }}'">🌟 VIP</button>
      <button class="btn" onclick="location.href='/page/guide?uid={{ uid }}'">📘 آموزش</button>
      <button class="btn" onclick="location.href='/page/support?uid={{ uid }}'">✉️ پشتیبانی</button>
    </div>
  </div>

  <a class="big-action" href="/page/ads-promo?uid={{ uid }}">تبلیغ کانال و کسب و کار شما📢</a>

  <div class="card">
    <div class="small">لینک دعوت شما:</div>
    <input class="ref" readonly id="ref-link" value="{{ referral_link }}">
    <div style="display:flex;gap:8px;margin-top:8px">
      <button class="btn secondary" onclick="navigator.clipboard.writeText(document.getElementById('ref-link').value);showToast('کپی شد')">📋 کپی لینک دعوت</button>
    </div>
    <div class="footer-note">زیرمجموعه‌ها: <strong>{{ total_subs }}</strong> — آنلاین: <strong>{{ online_subs }}</strong></div>
  </div>
</div>

<script>
(function(){
  const uid = "{{ uid }}";
  document.getElementById('uid-show').textContent = uid;
  document.getElementById('ref-link').value = "{{ referral_link }}";
  joinUidRoom(uid);
  fetchBalance(uid);
})();
</script>
</body></html>
"""

ADS_PAGE_TEMPLATE = """
<!doctype html><html lang="fa"><head><meta charset="utf-8"><title>{{ title }}</title>""" + BASE_STYLES + """</head><body>
<div class="container" style="margin-top:18px">
  <div class="card" style="display:flex;justify-content:space-between;align-items:center">
    <div><h2>{{ title }}</h2><div class="small">{{ desc }}</div></div>
    <div><button class="btn secondary" onclick="location.href='/'">🏠 بازگشت</button></div>
  </div>
  <div class="card" style="margin-top:12px">{{ body|safe }}</div>
</div>
</body></html>
"""

VIP_PAGE = """
<!doctype html><html lang="fa"><head><meta charset="utf-8"><title>VIP</title>""" + BASE_STYLES + """</head><body>
<div class="container">
  <div class="card" style="display:flex;justify-content:space-between;align-items:center">
    <h2>🌟 اشتراک VIP</h2>
    <button class="btn secondary" onclick="location.href='/'">🏠 بازگشت</button>
  </div>
  <div class="card">
    <div class="small">پلن‌ها:</div>
    <div style="display:flex;flex-direction:column;gap:10px;margin-top:10px">
      {% for key,p in packs.items() %}
        <div class="pack-card">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
            <div>
              <div style="font-weight:800">{{ p.label }}</div>
              <div class="small">مدت: {{ p.days }} روز — افزایش امتیاز: x{{ '%.2f'|format(p.multiplier) }}</div>
              <div style="margin-top:6px;" class="small">هر امتیاز = {{ toman_per_point }} تومان</div>
            </div>
            <div style="text-align:right;min-width:120px">
              <div style="font-weight:900;font-size:18px">{{ p.price }} تومان</div>
              <div style="margin-top:8px;display:flex;flex-direction:column;gap:8px">
                <form method="post" action="/create-vip-purchase" enctype="multipart/form-data" style="display:flex;flex-direction:column;gap:6px">
                  <input type="hidden" name="uid" value="{{ uid }}">
                  <input type="hidden" name="pack" value="{{ key }}">
                  <input type="file" name="screenshot" accept="image/*" required>
                  <input name="payment_ref" placeholder="شناسه تراکنش (اختیاری)" class="input">
                  <button class="btn" type="submit">📤 ارسال رسید</button>
                </form>
                <div style="display:flex;gap:6px">
                  <form method="post" action="/buy-vip-points" style="flex:1">
                    <input type="hidden" name="uid" value="{{ uid }}">
                    <input type="hidden" name="pack" value="{{ key }}">
                    <button class="btn secondary" type="submit" style="width:100%">⚡ خرید با امتیاز</button>
                  </form>
                  <button class="btn secondary" onclick="window.open('{{ reymit }}','_blank')">💳 خرید لینک</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      {% endfor %}
    </div>
  </div>
</div>
</body></html>
"""

ADS_PROMO_TEMPLATE = """
<!doctype html><html lang="fa"><head><meta charset="utf-8"><title>تبلیغات</title>""" + BASE_STYLES + """</head><body>
<div class="container" style="margin-top:18px">

  <div class="card" style="display:flex;justify-content:space-between;align-items:center">
    <h2>📢 تبلیغ کانال و کسب‌وکار</h2>
    <button class="btn secondary" onclick="location.href='/'">🏠 بازگشت</button>
  </div>

  <!-- Section 1: مشاهده پک های تبلیغ -->
  <div class="card">
    <h3>بخش ۱ — مشاهده پک‌های تبلیغ</h3>
    <div class="small">پک‌های تبلیغاتی موجود را مشاهده کنید:</div>
    <div style="display:flex;flex-direction:column;gap:10px;margin-top:10px">
      {% for key,p in promo.items() %}
        <div class="pack-card">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div>
              <div style="font-weight:800">{{ p.label }}</div>
              <div class="small">قیمت: {{ p.price }} تومان — اسلات‌ها: {{ p.slots }}</div>
            </div>
            <div style="min-width:100px;text-align:right;font-weight:900">{{ p.price }} تومان</div>
          </div>
        </div>
      {% endfor %}
    </div>
  </div>

  <!-- Section 2: خرید پک -->
  <div class="card">
    <h3>بخش ۲ — خرید پک</h3>
    <div class="small">می‌توانید پک مورد نیازتان را از اینجا بخرید (لینک درگاه یا پرداخت با امتیاز):</div>

    <div style="display:flex;flex-direction:column;gap:10px;margin-top:10px">
      {% for key,p in promo.items() %}
        <div class="pack-card">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div>
              <div style="font-weight:800">{{ p.label }}</div>
              <div class="small">قیمت: {{ p.price }} تومان</div>
            </div>
            <div style="min-width:160px;text-align:right">
              <form method="post" action="/create-ad-purchase" enctype="multipart/form-data" style="display:flex;flex-direction:column;gap:6px">
                <input type="hidden" name="uid" value="{{ uid }}">
                <input type="hidden" name="pack" value="{{ key }}">
                <input type="file" name="screenshot" accept="image/*" required>
                <input name="payment_ref" placeholder="شناسه تراکنش (اختیاری)" class="input">
                <button class="btn" type="submit">📤 ارسال رسید (تایید ادمین)</button>
              </form>
              <div style="display:flex;gap:6px;margin-top:6px">
                <form method="post" action="/buy-ad-points" style="flex:1">
                  <input type="hidden" name="uid" value="{{ uid }}">
                  <input type="hidden" name="pack" value="{{ key }}">
                  <button class="btn secondary" type="submit">⚡ خرید با امتیاز</button>
                </form>
                <button class="btn secondary" onclick="window.open('{{ reymit }}','_blank')">💳 خرید لینک</button>
              </div>
            </div>
          </div>
        </div>
      {% endfor %}
    </div>

    <div style="margin-top:12px">
      <h4>خریدهای شما (پک‌های تبلیغ)</h4>
      {% if purchases %}
        <div style="display:flex;flex-direction:column;gap:8px">
          {% for p in purchases %}
            <div class="pack-card">
              <div style="display:flex;justify-content:space-between;align-items:center">
                <div>
                  <div style="font-weight:800">PID: {{ p[0] }} — پکیج: {{ p[6] or '-' }}</div>
                  <div class="small">مبلغ: {{ p[2] }} تومان — وضعیت: {{ p[7] }}</div>
                </div>
                <div style="min-width:120px;text-align:right">
                  {% if p[7] == 'approved' %}
                    <a class="btn" href="/submit-ad?uid={{ uid }}&pid={{ p[0] }}">✍️ ثبت آگهی</a>
                  {% elif p[7] == 'pending' %}
                    <div class="small">در انتظار تایید ادمین</div>
                  {% else %}
                    <div class="small">وضعیت: {{ p[7] }}</div>
                  {% endif %}
                </div>
              </div>
            </div>
          {% endfor %}
        </div>
      {% else %}
        <div class="small">هیچ خریدی ثبت نکرده‌اید.</div>
      {% endif %}
    </div>
  </div>

  <!-- Section 3: ثبت آگهی (پس از تایید) -->
  <div class="card">
    <h3>بخش ۳ — ثبت آگهی</h3>
    <div class="small">پس از تایید پرداخت (ارسال رسید یا پرداخت با امتیاز)، از قسمت "خریدهای شما" روی دکمهٔ ثبت آگهی کلیک کنید یا PID را وارد کنید تا بتوانید آگهی شامل متن، لینک کانال و عکس را آپلود کنید.</div>
    <div style="margin-top:8px">
      <form method="get" action="/submit-ad">
        <input name="uid" type="hidden" value="{{ uid }}">
        <input name="pid" placeholder="شناسه خرید (PID) که تایید شده" class="input">
        <button class="btn" type="submit">✍️ رفتن به فرم ثبت آگهی</button>
      </form>
    </div>
  </div>

</div>
</body></html>
"""

SUBMIT_AD_TEMPLATE = """
<!doctype html><html lang="fa"><head><meta charset="utf-8"><title>ثبت آگهی</title>""" + BASE_STYLES + """</head><body>
<div class="container" style="margin-top:18px">
  <div class="card">
    <h2>ثبت آگهی</h2>
    {% if error %}
      <div class="small" style="color:#ffb3b3">{{ error }}</div>
    {% endif %}
    {% if allowed %}
      <form method="post" action="/submit-ad" enctype="multipart/form-data" style="display:flex;flex-direction:column;gap:8px">
        <input type="hidden" name="uid" value="{{ uid }}">
        <input type="hidden" name="pid" value="{{ pid }}">
        <input name="title" placeholder="عنوان آگهی" required class="input">
        <input name="channel_link" placeholder="لینک کانال/گروه" required class="input">
        <textarea name="description" placeholder="توضیحات" class="input"></textarea>
        <input name="phone" placeholder="شماره تماس" class="input">
        <div>
          <label class="small">عکس آگهی (اختیاری):</label>
          <input type="file" name="ad_image" accept="image/*" class="input">
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn" type="submit">ثبت آگهی</button>
          <a class="btn secondary" href="/">انصراف</a>
        </div>
      </form>
    {% else %}
      <div class="small">آگهی برای این شناسه خرید قابل ثبت نیست (ممکن است هنوز تایید نشده یا به کاربر دیگری تعلق داشته باشد).</div>
    {% endif %}
  </div>
</div>
</body></html>
"""

ENHANCED_PROFILE_TEMPLATE = """
<!doctype html><html lang="fa"><head><meta charset="utf-8"><title>پروفایل</title>""" + BASE_STYLES + """</head><body>
<div class="container">
  <div class="card" style="display:flex;justify-content:space-between;align-items:center">
    <div>
      <div style="font-weight:900;font-size:18px">🧾 پروفایل — کاربر {{ uid }}</div>
      <div class="small">امتیاز فعلی: <strong>{{ points }}</strong> — معادل <strong>{{ points_toman }} تومان</strong></div>
    </div>
    <div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end">
      <div class="badge">زیرمجموعه‌ها: {{ total_subs }}</div>
      <div class="badge">درآمد از زیرمجموعه: {{ affiliate_toman }} تومان</div>
    </div>
  </div>

  <div class="card" style="margin-top:12px">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <div><strong>پیشرفت تا حداقل برداشت</strong></div>
      <div class="small">{{ points }}/{{ min_withdraw }} امتیاز</div>
    </div>
    <div style="margin-top:8px;background:#052027;border-radius:8px;height:12px;overflow:hidden"><i style="display:block;height:100%;background:linear-gradient(90deg,#00d4ff,#6af0c2);width:{{ progress_pct }}%"></i></div>
    <div style="margin-top:10px" class="small">هر امتیاز = {{ toman_per_point }} تومان — با رسیدن به حداقل می‌توانید برداشت کنید.</div>
    <div style="margin-top:10px;display:flex;gap:8px;flex-direction:column">
      <button class="btn" onclick="location.href='/page/vip?uid={{ uid }}'">🌟 خرید VIP</button>
      <button class="btn secondary" onclick="location.href='/export-referrals?uid={{ uid }}'">📥 Export Referrals (CSV)</button>
    </div>
  </div>

  <div class="card" style="margin-top:12px">
    <h3>سابقهٔ آخرین فعالیت‌ها</h3>
    {% if activities %}
      <div class="table-responsive">
      <table class="table">
        <thead><tr><th>زمان</th><th>رویداد</th><th>امتیاز</th><th>تومان</th></tr></thead>
        <tbody>
          {% for a in activities %}
            <tr><td class="small">{{ a.created_at }}</td><td class="small">{{ a.kind }}</td><td>{{ a.points }}</td><td>{{ a.amount_toman }}</td></tr>
          {% endfor %}
        </tbody>
      </table>
      </div>
    {% else %}
      <div class="small">رویدادی یافت نشد.</div>
    {% endif %}
  </div>

  <div class="card" style="margin-top:12px">
    <h3>🏆 لیدربورد — ۱۰ کاربر برتر</h3>
    <ol style="margin-top:8px">
      {% for u in leaderboard %}
        <li style="margin-bottom:6px"><strong>{{ loop.index }}.</strong> کاربر <strong>{{ u.user_id }}</strong> — <span class="small">{{ u.points }} امتیاز</span></li>
      {% endfor %}
    </ol>
  </div>

  <div class="card" style="margin-top:12px">
    <h3>زیرمجموعه‌ها</h3>
    {% if subs %}
      <div class="table-responsive">
      <table class="table">
        <thead><tr><th>UID</th><th>آخرین فعالیت</th><th>کل امتیاز</th><th>درآمد (تومان)</th></tr></thead>
        <tbody>
          {% for s in subs %}
            <tr><td>{{ s.user_id }}</td><td class="small">{{ s.last_active }}</td><td>{{ s.total_points }}</td><td>{{ s.total_toman }}</td></tr>
          {% endfor %}
        </tbody>
      </table>
      </div>
    {% else %}
      <div class="small">زیرمجموعه‌ای ندارید.</div>
    {% endif %}
  </div>

  <div style="margin-top:12px" class="small"><a href="/">🏠 بازگشت</a></div>
</div>
</body></html>
"""

ADMIN_PURCHASES_TEMPLATE = """
<!doctype html><html lang="fa"><head><meta charset="utf-8"><title>خریدها</title>""" + BASE_STYLES + """</head><body>
<div class="container" style="margin-top:18px">
  <div class="card"><h2>📥 درخواست‌های خرید</h2>
  {% for p in purchases %}
    <div class="card" style="margin-top:8px">
      <div style="font-weight:800">#{{ p[0] }} — کاربر: {{ p[1] }} — {{ p[2] }} تومان — نوع: {{ p[5] }} {% if p[6] %}/ {{ p[6] }}{% endif %}</div>
      <div class="small">وضعیت: {{ p[7] }} — ref: {{ p[4] }}</div>
      <div class="small">سکرین‌شات: {% if p[3] %}<a href="/uploads/{{ p[3] }}" target="_blank">{{ p[3] }}</a>{% else %}—{% endif %}</div>
      <div style="display:flex;gap:8px;margin-top:8px;flex-direction:column">
        <form method="post" action="/admin/purchase/{{ p[0] }}/approve" style="display:flex;flex-direction:column;gap:6px">
          <input name="days" placeholder="روزها (اختیاری)" class="input">
          <input name="admin_note" placeholder="یادداشت (اختیاری)" class="input">
          <button class="btn" type="submit">✅ تایید</button>
        </form>
        <form method="post" action="/admin/purchase/{{ p[0] }}/reject" style="display:flex;flex-direction:column;gap:6px">
          <input name="admin_note" placeholder="دلیل رد (اختیاری)" class="input">
          <button class="btn secondary" type="submit">❌ رد</button>
        </form>
      </div>
    </div>
  {% endfor %}
  <div style="margin-top:12px"><a class="btn secondary" href="/admin">بازگشت</a></div>
  </div>
</div>
</body></html>
"""

ADMIN_WITHDRAWS_TEMPLATE = """
<!doctype html><html lang="fa"><head><meta charset="utf-8"><title>برداشت‌ها</title>""" + BASE_STYLES + """</head><body>
<div class="container" style="margin-top:18px">
  <div class="card"><h2>💸 درخواست‌های برداشت</h2>
  {% for w in withdraws %}
    <div class="card" style="margin-top:8px">
      <div style="font-weight:800">#{{ w[0] }} — کاربر: {{ w[1] }} — {{ w[2] }} امتیاز</div>
      <div class="small">مبلغ: {{ w[3] }} تومان — کارت: {{ w[4] }} — تلفن: {{ w[5] }} — وضعیت: {{ w[6] }}</div>
      <div style="display:flex;gap:8px;margin-top:8px">
        <form method="post" action="/admin/withdraw/{{ w[0] }}/approve" style="flex:1"><button class="btn" type="submit">✅ تایید</button></form>
        <form method="post" action="/admin/withdraw/{{ w[0] }}/reject" style="flex:1"><button class="btn secondary" type="submit">❌ رد</button></form>
      </div>
    </div>
  {% endfor %}
  <div style="margin-top:12px"><a class="btn secondary" href="/admin">بازگشت</a></div>
  </div>
</div>
</body></html>
"""

ADMIN_ADS_TEMPLATE = """
<!doctype html><html lang="fa"><head><meta charset="utf-8"><title>آگهی‌ها</title>""" + BASE_STYLES + """</head><body>
<div class="container" style="margin-top:18px">
  <div class="card"><h2>📋 آگهی‌ها</h2>
    <div class="table-responsive">
    <table class="table">
      <thead><tr><th>ID</th><th>کاربر</th><th>عنوان</th><th>وضعیت</th><th>عملیات</th></tr></thead>
      <tbody>
        {% for a in ads %}
          <tr>
            <td>{{ a[0] }}</td>
            <td>{{ a[2] }}</td>
            <td>{{ a[3] }}</td>
            <td>{{ a[8] }}</td>
            <td>
              <form method="post" action="/admin/ad/{{ a[0] }}/toggle" style="display:inline">
                <button class="btn" type="submit">{{ 'غیرفعال' if a[8]=='active' else 'فعال' }}</button>
              </form>
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
    </div>
    <div style="margin-top:12px"><a class="btn secondary" href="/admin">بازگشت</a></div>
  </div>
</div>
</body></html>
"""

GUIDE_TEMPLATE = """
<!doctype html><html lang="fa"><head><meta charset="utf-8"><title>راهنما</title>""" + BASE_STYLES + """</head><body>
<div class="container">
  <div class="card"><h2>📘 آموزش مختصر</h2>
    <div class="small">راهنمای سریع استفاده از ربات و پنل</div>
    <ol style="margin-top:10px">
      <li>در صفحه اصلی، UID خود را ببینید و لینک دعوت را کپی کنید.</li>
      <li>برای کسب امتیاز: کلیپ ببینید، لینک‌ها را باز کنید یا در کانال عضو شوید.</li>
      <li>برای خرید VIP: صفحه VIP را باز کنید، یکی از پلن‌ها را انتخاب و رسید را آپلود کنید یا با امتیاز خرید کنید.</li>
      <li>برای تبلیغات: صفحه تبلیغ کانال را باز کنید؛ پک موردنظر را بخرید، پس از تایید ادمین آگهی خود را ثبت کنید.</li>
      <li>در پنل ادمین، سفارش‌ها را تایید یا رد می‌کنند؛ در صورت تایید، نوتیف دریافت خواهید کرد.</li>
    </ol>
    <div style="margin-top:12px" class="small">نکته: برای تجربه بهتر، صفحات را در موبایل به‌صورت افقی یا عمودی امتحان کنید؛ طراحی واکنش‌گراست.</div>
  </div>
</div>
</body></html>
"""

SUPPORT_TEMPLATE = """
<!doctype html><html lang="fa"><head><meta charset="utf-8"><title>پشتیبانی</title>""" + BASE_STYLES + """</head><body>
<div class="container">
  <div class="card"><h2>✉️ پشتیبانی</h2>
    <p class="small">برای تماس با پشتیبانی گزینه‌های زیر در دسترس است:</p>
    <ul style="margin-top:8px">
      <li><strong>تلگرام:</strong> @your_admin</li>
      <li><strong>ایمیل:</strong> support@example.com</li>
      <li><strong>فرم پیام درون‌رباتی:</strong> از صفحه پشتیبانی داخل ربات استفاده کنید.</li>
    </ul>
    <div style="margin-top:12px" class="small">ساعات کاری: 9:00 — 22:00</div>
  </div>
</div>
</body></html>
"""

# ---------- Routes ----------
@app.route("/")
def home():
    uid = flask_request.args.get("uid", "1")
    ref = flask_request.args.get("ref")
    try:
        uid_i = int(uid)
    except:
        uid_i = 1
    try:
        ref_i = int(ref) if ref else None
    except:
        ref_i = None
    create_user(uid_i, referrer=ref_i)
    u = get_user(uid_i)
    points = u[1] if u else 0
    pts_toman = points * TOMAN_PER_POINT
    subs = get_subscribers_of(uid_i)
    total_subs = len(subs)
    now = int(time.time())
    online_subs = sum(1 for s in subs if s[1] and (now - s[1]) <= ONLINE_SECONDS)
    referral_link = f"{flask_request.url_root}?uid={uid_i}&ref={uid_i}"
    return render_template_string(HOME_TEMPLATE,
                                  uid=uid_i,
                                  points=points,
                                  points_toman=pts_toman,
                                  referral_link=referral_link,
                                  total_subs=total_subs,
                                  online_subs=online_subs,
                                  clip=POINTS_PER_CLIP,
                                  link=POINTS_PER_LINK,
                                  join=POINTS_PER_JOIN)

# Ads pages (clip/link/join)
@app.route("/page/ads/clip")
def page_ads_clip():
    uid = flask_request.args.get("uid", "1")
    body = render_template_string('''
      <div style="text-align:center">
        <div style="font-weight:900;font-size:18px">🎬 کلیپ پر امتیاز</div>
        <div class="small" style="margin-top:6px">برای دریافت امتیاز دکمه را بزنید و ویدیو را تماشا کنید.</div>
        <div style="margin-top:12px"><button class="btn" id="startBtn">▶️ شروع — دریافت {{ points }} امتیاز</button></div>
      </div>
      <script>
      document.getElementById('startBtn').addEventListener('click', function(){
        const uid = "{{ uid }}";
        this.disabled = true; this.textContent = '⏳ در حال پردازش...';
        setTimeout(()=> {
          fetch('/earn-clip', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({uid:uid})})
            .then(r=>r.json()).then(d=>{ showToast('نتیجه: ' + JSON.stringify(d)); fetchBalance(uid); location.href='/'; });
        }, 1800);
      });
      </script>
    ''', uid=uid, points=POINTS_PER_CLIP)
    return render_template_string(ADS_PAGE_TEMPLATE, title="تماشای کلیپ 🎬", desc="با مشاهده کلیپ‌ها امتیاز بگیرید.", body=body)

@app.route("/page/ads/link")
def page_ads_link():
    uid = flask_request.args.get("uid", "1")
    body = render_template_string('''
      <div style="text-align:center">
        <div style="font-weight:900;font-size:18px">🔗 بازدید لینک</div>
        <div class="small" style="margin-top:6px">روی لینک کلیک کنید و سپس ثبت بازدید را بزنید.</div>
        <div style="margin-top:12px"><a class="btn" href="https://example.com" target="_blank">🌐 باز کردن لینک</a></div>
        <div style="margin-top:12px"><button class="btn" id="registerLink">✅ ثبت بازدید — دریافت {{ points }} امتیاز</button></div>
      </div>

      <script>
      document.getElementById('registerLink').addEventListener('click', function(){
        const uid = "{{ uid }}";
        fetch('/earn-link', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({uid:uid})})
          .then(r=>r.json()).then(d=>{ showToast('نتیجه: ' + JSON.stringify(d)); fetchBalance(uid); location.href='/'; });
      });
      </script>
    ''', uid=uid, points=POINTS_PER_LINK)
    return render_template_string(ADS_PAGE_TEMPLATE, title="بازدید لینک 🔗", desc="بازدید لینک‌ها و کسب امتیاز.", body=body)

@app.route("/page/ads/join")
def page_ads_join():
    uid = flask_request.args.get("uid", "1")
    body = render_template_string('''
      <div style="text-align:center">
        <div style="font-weight:900;font-size:18px">👥 عضویت در کانال</div>
        <div class="small" style="margin-top:6px">ابتدا به کانال بپیوندید سپس تایید را بزنید.</div>
        <div style="margin-top:12px"><a class="btn" href="https://t.me/sample_channel" target="_blank">🔔 رفتن به کانال</a></div>
        <div style="margin-top:12px"><button class="btn" id="confirmJoin">✍️ ثبت عضویت — دریافت {{ points }} امتیاز</button></div>
      </div>

      <script>
      document.getElementById('confirmJoin').addEventListener('click', function(){
        const uid = "{{ uid }}";
        fetch('/earn-join', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({uid:uid})})
          .then(r=>r.json()).then(d=>{ showToast('نتیجه: ' + JSON.stringify(d)); fetchBalance(uid); location.href='/'; });
      });
      </script>
    ''', uid=uid, points=POINTS_PER_JOIN)
    return render_template_string(ADS_PAGE_TEMPLATE, title="عضویت در کانال 👥", desc="عضویت و تایید برای امتیاز.", body=body)

# Secure page_ads_promo: log exceptions and optionally show traceback when FLASK_DEBUG=1
@app.route("/page/ads-promo")
def page_ads_promo():
    uid = flask_request.args.get("uid", "1")
    try:
        uid_i = int(uid)
    except:
        uid_i = 1

    try:
        purchases = get_user_purchases(uid_i, types=['ad', 'ad_join'])
        return render_template_string(
            ADS_PROMO_TEMPLATE,
            uid=uid,
            promo=AD_PROMO_PACKS,
            join=AD_JOIN_PACKS,
            reymit=REYMIT_BASE,
            purchases=purchases,
        )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log.error("Error in /page/ads-promo for uid=%s: %s\n%s", uid, e, tb)
        if os.environ.get("FLASK_DEBUG") == "1":
            return f"<pre>{tb}</pre>", 500
        return "Internal Server Error", 500

# VIP endpoints
@app.route("/page/vip")
def page_vip():
    uid = flask_request.args.get("uid", "1")
    return render_template_string(VIP_PAGE, uid=uid, packs=VIP_PACKS, reymit=REYMIT_BASE, toman_per_point=TOMAN_PER_POINT)

@app.route("/create-vip-purchase", methods=["POST"])
def create_vip_purchase():
    uid = flask_request.form.get("uid")
    pack = flask_request.form.get("pack")
    payment_ref = flask_request.form.get("payment_ref", "") or None
    if not uid or not pack:
        return "uid and pack required", 400
    if pack not in VIP_PACKS:
        return "invalid pack", 400
    if 'screenshot' not in flask_request.files:
        return "no file", 400
    file = flask_request.files['screenshot']
    if file.filename == '':
        return "no selected file", 400
    if not file.filename.lower().endswith(('.png','.jpg','.jpeg','.webp')):
        return "invalid file type", 400
    ext = Path(file.filename).suffix
    fn = f"vip_proof_{uid}_{pack}_{int(time.time())}_{secrets.token_hex(6)}{ext}"
    path = os.path.join(UPLOAD_FOLDER, fn)
    file.save(path)
    amount = VIP_PACKS[pack]['price']
    try:
        uid_i = int(uid)
    except:
        return "invalid uid", 400
    pid = create_purchase(uid_i, amount, fn, payment_ref, ptype='vip', subtype=pack)
    log.info("New VIP purchase uploaded pid=%s uid=%s pack=%s file=%s", pid, uid_i, pack, fn)
    return render_template_string("<p>درخواست VIP ارسال شد. PID: {{ pid }}. پس از تایید ادمین فعال می‌شود.</p><p><a href='/page/vip?uid={{ uid }}'>بازگشت</a></p>", pid=pid, uid=uid)

@app.route("/buy-vip-points", methods=["POST"])
def buy_vip_points():
    uid = flask_request.form.get("uid")
    pack = flask_request.form.get("pack")
    if not uid or not pack:
        return "uid and pack required", 400
    if pack not in VIP_PACKS:
        return "invalid pack", 400
    try:
        uid_i = int(uid)
    except:
        return "invalid uid", 400
    amount_toman = VIP_PACKS[pack]['price']
    required_points = amount_toman // TOMAN_PER_POINT
    u = get_user(uid_i)
    if not u:
        create_user(uid_i); u = get_user(uid_i)
    current_points = u[1]
    if current_points < required_points:
        return render_template_string("<p>امتیاز کافی ندارید. شما {{ cur }} امتیاز دارید و نیاز به {{ req }} امتیاز دارید.</p><p><a href='/page/vip?uid={{ uid }}'>بازگشت</a></p>", cur=current_points, req=required_points, uid=uid)
    change_points(uid_i, -required_points)
    pid = create_purchase(uid_i, amount_toman, screenshot_filename=None, payment_ref=f"points:{required_points}", ptype='vip', subtype=pack)
    log.info("VIP purchase with points created pid=%s uid=%s pack=%s points=%s", pid, uid_i, pack, required_points)
    return render_template_string("<p>درخواست VIP با امتیاز ارسال شد. PID: {{ pid }} — امتیاز {{ pts }} کسر شد.</p><p><a href='/page/vip?uid={{ uid }}'>بازگشت</a></p>", pts=required_points, pid=pid, uid=uid)

# Ads promo endpoints (create-ad-purchase, buy-ad-points implemented earlier)
@app.route("/create-ad-purchase", methods=["POST"])
def create_ad_purchase():
    uid = flask_request.form.get("uid")
    pack = flask_request.form.get("pack")
    payment_ref = flask_request.form.get("payment_ref", "") or None
    if not uid or not pack:
        return "uid and pack required", 400
    if pack in AD_PROMO_PACKS:
        amount = AD_PROMO_PACKS[pack]['price']
        ptype = 'ad'
        subtype = pack
    elif pack in AD_JOIN_PACKS:
        amount = AD_JOIN_PACKS[pack]['price']
        ptype = 'ad_join'
        subtype = pack
    else:
        return "invalid pack", 400
    if 'screenshot' not in flask_request.files:
        return "no file", 400
    file = flask_request.files['screenshot']
    if file.filename == '':
        return "no selected file", 400
    if not file.filename.lower().endswith(('.png','.jpg','.jpeg','.webp')):
        return "invalid file type", 400
    ext = Path(file.filename).suffix
    fn = f"ad_proof_{uid}_{pack}_{int(time.time())}_{secrets.token_hex(6)}{ext}"
    path = os.path.join(UPLOAD_FOLDER, fn)
    file.save(path)
    try:
        uid_i = int(uid)
    except:
        return "invalid uid", 400
    pid = create_purchase(uid_i, amount, fn, payment_ref, ptype=ptype, subtype=pack)
    log.info("New AD purchase uploaded pid=%s uid=%s pack=%s file=%s", pid, uid_i, pack, fn)
    return render_template_string("<p>درخواست تبلیغ ارسال شد. PID: {{ pid }}.</p><p><a href='/page/ads-promo?uid={{ uid }}'>بازگشت</a></p>", pid=pid, uid=uid)

@app.route("/buy-ad-points", methods=["POST"])
def buy_ad_points():
    uid = flask_request.form.get("uid")
    pack = flask_request.form.get("pack")
    if not uid or not pack:
        return "uid and pack required", 400
    if pack in AD_PROMO_PACKS:
        amount = AD_PROMO_PACKS[pack]['price']
        ptype = 'ad'
    elif pack in AD_JOIN_PACKS:
        amount = AD_JOIN_PACKS[pack]['price']
        ptype = 'ad_join'
    else:
        return "invalid pack", 400
    try:
        uid_i = int(uid)
    except:
        return "invalid uid", 400
    required_points = amount // TOMAN_PER_POINT
    u = get_user(uid_i)
    if not u:
        create_user(uid_i); u = get_user(uid_i)
    if u[1] < required_points:
        return render_template_string("<p>امتیاز کافی ندارید. شما {{ cur }} امتیاز دارید و نیاز به {{ req }} امتیاز دارید.</p><p><a href='/page/ads-promo?uid={{ uid }}'>بازگشت</a></p>", cur=u[1], req=required_points, uid=uid)
    change_points(uid_i, -required_points)
    pid = create_purchase(uid_i, amount, screenshot_filename=None, payment_ref=f"points:{required_points}", ptype=ptype, subtype=pack)
    log.info("AD purchase with points pid=%s uid=%s pack=%s pts=%s", pid, uid_i, pack, required_points)
    return render_template_string("<p>درخواست تبلیغ با امتیاز ارسال شد. PID: {{ pid }} — امتیاز {{ pts }} کسر شد.</p><p><a href='/page/ads-promo?uid={{ uid }}'>بازگشت</a></p>", pts=required_points, pid=pid, uid=uid)

# Submit ad (after approval) - supports image upload and stores ads.image
@app.route("/submit-ad", methods=["GET","POST"])
def submit_ad():
    if flask_request.method == "GET":
        uid = flask_request.args.get("uid")
        pid = flask_request.args.get("pid")
        error = None
        allowed = False
        if not uid or not pid:
            error = "uid و pid لازم است."
            return render_template_string(SUBMIT_AD_TEMPLATE, allowed=False, error=error, uid=uid, pid=pid)
        try:
            uid_i = int(uid); pid_i = int(pid)
        except:
            error = "پارامترهای نامعتبر"
            return render_template_string(SUBMIT_AD_TEMPLATE, allowed=False, error=error, uid=uid, pid=pid)
        conn = _get_conn(); c = conn.cursor()
        c.execute("SELECT id,user_id,type,status FROM purchases WHERE id=?", (pid_i,))
        r = c.fetchone(); conn.close()
        if not r:
            error = "خرید یافت نشد"
            return render_template_string(SUBMIT_AD_TEMPLATE, allowed=False, error=error, uid=uid, pid=pid)
        _, owner, ptype, status = r
        if owner != uid_i:
            error = "این خرید به شما تعلق ندارد"
            return render_template_string(SUBMIT_AD_TEMPLATE, allowed=False, error=error, uid=uid, pid=pid)
        if status != "approved":
            error = "خرید هنوز تایید نشده"
            return render_template_string(SUBMIT_AD_TEMPLATE, allowed=False, error=error, uid=uid, pid=pid)
        if ptype not in ("ad", "ad_join"):
            error = "این خرید مربوط به ارسال آگهی نیست"
            return render_template_string(SUBMIT_AD_TEMPLATE, allowed=False, error=error, uid=uid, pid=pid)
        return render_template_string(SUBMIT_AD_TEMPLATE, allowed=True, error=None, uid=uid, pid=pid)
    else:
        uid = flask_request.form.get("uid")
        pid = flask_request.form.get("pid")
        title = flask_request.form.get("title")
        channel_link = flask_request.form.get("channel_link")
        description = flask_request.form.get("description")
        phone = flask_request.form.get("phone")
        if not uid or not pid or not title or not channel_link:
            return "ورودی‌ها ناقص هستند", 400
        try:
            uid_i = int(uid); pid_i = int(pid)
        except:
            return "پارامتر نامعتبر", 400
        conn = _get_conn(); c = conn.cursor()
        c.execute("SELECT id,user_id,status FROM purchases WHERE id=?", (pid_i,))
        r = c.fetchone()
        if not r or r[1] != uid_i or r[2] != "approved":
            conn.close(); return "خرید معتبر نیست یا تایید نشده", 400
        ts = int(time.time())
        # handle ad image if uploaded
        image_filename = None
        if 'ad_image' in flask_request.files:
            img = flask_request.files['ad_image']
            if img and img.filename:
                if img.filename.lower().endswith(('.png','.jpg','.jpeg','.webp')):
                    ext = Path(img.filename).suffix
                    image_filename = f"ad_image_{uid}_{pid}_{int(time.time())}_{secrets.token_hex(6)}{ext}"
                    img.save(os.path.join(UPLOAD_FOLDER, image_filename))
                else:
                    conn.close()
                    return "فرمت تصویر معتبر نیست", 400
        c.execute("INSERT INTO ads (purchase_id, user_id, title, channel_link, description, image, phone, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                  (pid_i, uid_i, title, channel_link, description, image_filename, phone, "active", ts))
        conn.commit(); conn.close()
        log.info("New ad created by uid=%s pid=%s title=%s image=%s", uid_i, pid_i, title, image_filename)
        return render_template_string("<p>آگهی شما ثبت شد و هم‌اکنون فعال است.</p><p><a href='/'>بازگشت</a></p>")

# Award logic (VIP multipliers + affiliate scaling)
def _award(uid, kind, base_points, min_interval, last_field):
    try:
        uid_i = int(uid)
    except:
        return {"ok": False, "error": "invalid uid"}
    u = get_user(uid_i)
    if not u:
        create_user(uid_i); u = get_user(uid_i)
    last_ts = u[5] if last_field == 'last_clip_ts' else (u[6] if last_field=='last_link_ts' else u[7])
    now = int(time.time())
    if now - (last_ts or 0) < min_interval:
        return {"ok": False, "error": "too_soon", "next_in": min_interval - (now - (last_ts or 0))}
    awarded_points = apply_vip_multiplier_for_points(uid_i, base_points)
    change_points(uid_i, awarded_points)
    set_last_ts(uid_i, last_field, now)
    amount = log_earning_event(uid_i, kind, awarded_points)
    update_last_active(uid_i)
    # Affiliate credit with referrer's VIP multiplier
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT referrer FROM users WHERE user_id=?", (uid_i,))
    row = c.fetchone()
    if row and row[0]:
        try:
            ref_i = int(row[0])
            ref_mult = get_referrer_multiplier(ref_i)
            aff_points = int(round(awarded_points * AFFILIATE_SHARE * ref_mult))
            if aff_points > 0 and ref_i != uid_i:
                change_points(ref_i, aff_points)
                log_earning_event(ref_i, f"affiliate_{kind}", aff_points)
        except Exception:
            pass
    return {"ok": True, "points_awarded": awarded_points, "amount_toman": amount}

@app.route("/earn-clip", methods=["POST"])
def earn_clip():
    data = flask_request.get_json(force=True, silent=True) or {}
    uid = data.get("uid")
    res = _award(uid, "clip", POINTS_PER_CLIP, MIN_CLIP_INTERVAL, "last_clip_ts")
    return jsonify(res)

@app.route("/earn-link", methods=["POST"])
def earn_link():
    data = flask_request.get_json(force=True, silent=True) or {}
    uid = data.get("uid")
    res = _award(uid, "link", POINTS_PER_LINK, MIN_LINK_INTERVAL, "last_link_ts")
    return jsonify(res)

@app.route("/earn-join", methods=["POST"])
def earn_join():
    data = flask_request.get_json(force=True, silent=True) or {}
    uid = data.get("uid")
    res = _award(uid, "join", POINTS_PER_JOIN, MIN_JOIN_INTERVAL, "last_join_ts")
    return jsonify(res)

# Balance / profile / referral / exports / recent activities / leaderboard
@app.route("/balance-json")
def balance_json():
    uid = flask_request.args.get("uid")
    if not uid:
        return jsonify({"ok": False, "error": "uid required"}), 400
    try:
        uid_i = int(uid)
    except:
        return jsonify({"ok": False, "error": "invalid uid"}), 400
    u = get_user(uid_i)
    if not u:
        create_user(uid_i); u = get_user(uid_i)
    points = u[1]
    last_active = u[9] or 0
    online = (int(time.time()) - last_active) <= ONLINE_SECONDS
    aff = get_affiliate_earnings(uid_i)
    vip = user_vip_info(uid_i)
    return jsonify({"ok": True, "points": points, "card": u[2], "phone": u[3], "online": online, "affiliate_points": aff["points"], "affiliate_toman": aff["amount_toman"], "vip": vip})

@app.route("/page/profile")
def page_profile():
    uid = flask_request.args.get("uid")
    if not uid:
        return "uid required", 400
    try:
        uid_i = int(uid)
    except:
        return "invalid uid", 400
    create_user(uid_i)
    u = get_user(uid_i)
    points = u[1]; card = u[2]; phone = u[3]
    aff = get_affiliate_earnings(uid_i)
    total_earned = get_total_earnings_toman(uid_i)
    subs_raw = get_subscribers_of(uid_i)
    subs = []
    now = int(time.time())
    for s in subs_raw:
        sub_id, last_active = s
        conn = _get_conn(); c = conn.cursor()
        c.execute("SELECT SUM(points), SUM(amount_toman) FROM earning_events WHERE user_id=?", (sub_id,))
        r = c.fetchone(); conn.close()
        total_points = r[0] or 0; total_toman = r[1] or 0
        is_online = bool(last_active and (now - last_active) <= ONLINE_SECONDS)
        subs.append({"user_id": sub_id, "last_active": time.strftime("%Y-%m-%d %H:%M", time.localtime(last_active)) if last_active else "-", "total_points": total_points, "total_toman": total_toman, "is_online": is_online})
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT kind, points, amount_toman, created_at FROM earning_events WHERE user_id=? ORDER BY created_at DESC LIMIT 10", (uid_i,))
    rows = c.fetchall(); conn.close()
    activities = [{"kind": r[0], "points": r[1], "amount_toman": r[2], "created_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(r[3]))} for r in rows]
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT user_id, points FROM users ORDER BY points DESC LIMIT 10")
    lb = c.fetchall(); conn.close()
    leaderboard = [{"user_id": r[0], "points": r[1]} for r in lb]
    vip = user_vip_info(uid_i)
    total_subs = len(subs); online_subs = sum(1 for s in subs if s["is_online"])
    referral_link = f"{flask_request.url_root}?uid={uid_i}&ref={uid_i}"
    return render_template_string(ENHANCED_PROFILE_TEMPLATE,
                                  uid=uid_i,
                                  points=points,
                                  points_toman=points*TOMAN_PER_POINT,
                                  referral_link=referral_link,
                                  total_subs=total_subs,
                                  online_subs=online_subs,
                                  subs=subs,
                                  activities=activities,
                                  leaderboard=leaderboard,
                                  affiliate_toman=aff["amount_toman"],
                                  affiliate_points=aff["points"],
                                  total_earned_toman=total_earned,
                                  min_withdraw=MIN_WITHDRAW_POINTS,
                                  progress_pct=int(min(100, (points / MIN_WITHDRAW_POINTS) * 100)) if MIN_WITHDRAW_POINTS>0 else 100,
                                  toman_per_point=TOMAN_PER_POINT)

@app.route("/page/referral")
def page_referral():
    uid = flask_request.args.get("uid")
    if not uid:
        return "uid required", 400
    try:
        uid_i = int(uid)
    except:
        return "invalid uid", 400
    subs_raw = get_subscribers_of(uid_i)
    subs = []
    now = int(time.time())
    conn = _get_conn(); c = conn.cursor()
    for s in subs_raw:
        sub_id, last_active = s
        c.execute("SELECT SUM(points), SUM(amount_toman) FROM earning_events WHERE user_id=?", (sub_id,))
        r = c.fetchone()
        total_points = r[0] or 0; total_toman = r[1] or 0
        is_online = bool(last_active and (now - last_active) <= ONLINE_SECONDS)
        subs.append({"user_id": sub_id, "last_active": time.strftime("%Y-%m-%d %H:%M", time.localtime(last_active)) if last_active else "-", "total_points": total_points, "total_toman": total_toman, "is_online": is_online})
    conn.close()
    aff = get_affiliate_earnings(uid_i)
    total = len(subs); online = sum(1 for s in subs if s["is_online"]); link = f"{flask_request.url_root}?uid={uid_i}&ref={uid_i}"
    return render_template_string("""
<!doctype html><html lang="fa"><head><meta charset="utf-8">""" + BASE_STYLES + """</head><body>
<div class="container" style="margin-top:18px">
  <div class="card"><h2>🤝 صفحه معرفی</h2>
    <div class="small">لینک دعوت شما:</div>
    <input readonly class="ref" value="{{ link }}">
    <div style="margin-top:10px" class="small">تعداد زیرمجموعه‌ها: <strong>{{ total }}</strong> — آنلاین: <strong>{{ online }}</strong></div>
    <div style="margin-top:8px" class="small">درآمد از زیرمجموعه: <strong>{{ affiliate_toman }} تومان</strong> — ({{ affiliate_points }} امتیاز)</div>
  </div>
  <div class="card" style="margin-top:12px">
    <h3>لیست زیرمجموعه‌ها</h3>
    {% if subs %}
      <div class="table-responsive"><table class="table"><thead><tr><th>UID</th><th>آخرین فعالیت</th><th>امتیازات</th><th>درآمد (تومان)</th><th>وضعیت</th></tr></thead>
        <tbody>{% for s in subs %}<tr><td>{{ s.user_id }}</td><td class="small">{{ s.last_active }}</td><td>{{ s.total_points }}</td><td>{{ s.total_toman }}</td><td class="small">{{ '✅' if s.is_online else '⚪️' }}</td></tr>{% endfor %}</tbody></table></div>
    {% else %}
      <div class="small">هیچ زیرمجموعه‌ای ندارید.</div>
    {% endif %}
  </div>
  <div style="margin-top:12px" class="small"><a href="/">🏠 بازگشت</a></div>
</div>
</body></html>
""", link=link, total=total, online=online, subs=subs, affiliate_toman=aff["amount_toman"], affiliate_points=aff["points"])

@app.route("/export-referrals")
def export_referrals():
    uid = flask_request.args.get("uid")
    if not uid:
        return "uid required", 400
    try:
        uid_i = int(uid)
    except:
        return "invalid uid", 400
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT user_id, last_active FROM users WHERE referrer=?", (uid_i,))
    rows = c.fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["sub_uid", "last_active", "total_points", "total_toman", "is_online"])
    now = int(time.time())
    for r in rows:
        sub_uid, last_active = r
        c.execute("SELECT SUM(points), SUM(amount_toman) FROM earning_events WHERE user_id=?", (sub_uid,))
        s = c.fetchone()
        total_points = s[0] or 0
        total_toman = s[1] or 0
        is_online = 1 if last_active and (now - last_active) <= ONLINE_SECONDS else 0
        writer.writerow([sub_uid, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_active)) if last_active else "-", total_points, total_toman, is_online])
    conn.close()
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=referrals_{uid}.csv"})

@app.route("/recent-activities")
def recent_activities():
    uid = flask_request.args.get("uid")
    if not uid:
        return jsonify({"ok": False, "error": "uid required"}), 400
    try:
        uid_i = int(uid)
    except:
        return jsonify({"ok": False, "error": "invalid uid"}), 400
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT kind, points, amount_toman, created_at FROM earning_events WHERE user_id=? ORDER BY created_at DESC LIMIT 30", (uid_i,))
    rows = c.fetchall(); conn.close()
    data = []
    for r in rows:
        data.append({"kind": r[0], "points": r[1], "amount_toman": r[2], "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r[3]))})
    return jsonify({"ok": True, "activities": data})

@app.route("/leaderboard")
def leaderboard():
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT user_id, points FROM users ORDER BY points DESC LIMIT 10")
    rows = c.fetchall(); conn.close()
    html = "<h2>لیدربورد — ۱۰ کاربر برتر</h2><ol>"
    for u in rows:
        html += f"<li>کاربر {u[0]} — {u[1]} امتیاز</li>"
    html += "</ol><p><a href='/'>بازگشت</a></p>"
    return html

# ---------- Admin routes ----------
@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if flask_request.method == "POST":
        pw = flask_request.form.get("pass","")
        if pw == ADMIN_PASS:
            session["is_admin"] = True
            return redirect(url_for("admin_index"))
        return "invalid", 403
    login_tpl = """
    <!doctype html><html lang="fa"><head><meta charset="utf-8">""" + BASE_STYLES + """</head><body>
    <div class="container" style="margin-top:18px"><div class="card" style="max-width:420px;margin:0 auto"><h2>🔐 ورود ادمین</h2>
    <form method="post"><input name="pass" type="password" placeholder="رمز عبور" class="input"><div style="margin-top:12px"><button class="btn" type="submit">ورود</button></div></form></div></div></body></html>
    """
    return render_template_string(login_tpl)

@app.route("/admin")
@admin_required
def admin_index():
    # Added two admin buttons: Settings and Stats — other texts/buttons left unchanged.
    panel = """<!doctype html><html lang="fa"><head><meta charset="utf-8">""" + BASE_STYLES + """</head><body>
    <div class="container" style="margin-top:18px"><div class="card"><h2>⚙️ پنل ادمین</h2>
    <div style="display:flex;flex-direction:column;gap:8px">
      <a class="btn" href="/admin/purchases">📥 خریدها</a>
      <a class="btn" href="/admin/withdraws">💸 برداشت‌ها</a>
      <a class="btn" href="/admin/ads">📢 آگهی‌ها</a>
      <a class="btn" href="/admin/settings">✏️ ویرایش تنظیمات ربات</a>
      <a class="btn" href="/admin/stats">📊 آمار و گزارش‌ها</a>
      <a class="btn secondary" href="/admin/logout">🚪 خروج</a>
    </div>
    </div></div></body></html>"""
    return render_template_string(panel)

@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin_login"))

@app.route("/admin/purchases")
@admin_required
def admin_purchases():
    rows = list_purchases()
    return render_template_string(ADMIN_PURCHASES_TEMPLATE, purchases=rows)

@app.route("/admin/purchase/<int:pid>/approve", methods=["POST"])
@admin_required
def admin_purchase_approve(pid):
    vpn_user = flask_request.form.get("vpn_user")
    vpn_pass = flask_request.form.get("vpn_pass")
    days = flask_request.form.get("days")
    admin_note = flask_request.form.get("admin_note")
    try:
        days_i = int(days) if days else None
    except:
        days_i = None
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT user_id, amount, type, subtype, status, payment_ref FROM purchases WHERE id=?", (pid,))
    row = c.fetchone()
    if row:
        user_id, amount, ptype, subtype, status, payment_ref = row
        if status == "approved":
            conn.close(); return redirect(url_for("admin_purchases"))
        if ptype == 'vip':
            if days_i:
                add_days = days_i; selected_pack = subtype
            else:
                selected_pack = subtype
                add_days = VIP_PACKS.get(selected_pack, {}).get('days', 30)
            now = int(time.time())
            u = get_user(user_id)
            current_exp = u[10] or 0
            start_from = current_exp if current_exp and current_exp > now else now
            new_exp = int(start_from + add_days * 24 * 3600)
            vip_multiplier = VIP_PACKS.get(selected_pack, {}).get('multiplier', 1.0)
            vip_tier = selected_pack
            c.execute("UPDATE users SET vip_expires=?, vip_tier=?, vip_multiplier=? WHERE user_id=?", (new_exp, vip_tier, vip_multiplier, user_id))
            conn.commit()
            update_purchase_status(pid, "approved", admin_note=admin_note, vpn_user=None, vpn_pass=None, vpn_expires=new_exp)
            try:
                socketio.emit('purchase_approved', {'pid': pid, 'uid': user_id, 'amount': amount, 'vip_days': add_days, 'vip_tier': vip_tier}, room=f"user_{user_id}")
            except Exception as e:
                log.warning("socket emit failed: %s", e)
        elif ptype in ('ad','ad_join'):
            update_purchase_status(pid, "approved", admin_note=admin_note, vpn_user=None, vpn_pass=None, vpn_expires=None)
            try:
                socketio.emit('purchase_approved', {'pid': pid, 'uid': user_id, 'amount': amount}, room=f"user_{user_id}")
            except:
                pass
        else:
            if days_i:
                vpn_expires = int(time.time()) + days_i * 24 * 3600
            else:
                vpn_expires = None
            update_purchase_status(pid, "approved", admin_note=admin_note, vpn_user=vpn_user or None, vpn_pass=vpn_pass or None, vpn_expires=vpn_expires)
            try:
                socketio.emit('purchase_approved', {'pid': pid, 'uid': user_id, 'amount': amount}, room=f"user_{user_id}")
            except:
                pass
    conn.close()
    return redirect(url_for("admin_purchases"))

@app.route("/admin/purchase/<int:pid>/reject", methods=["POST"])
@admin_required
def admin_purchase_reject(pid):
    admin_note = flask_request.form.get("admin_note")
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT user_id, payment_ref, amount FROM purchases WHERE id=?", (pid,))
    r = c.fetchone()
    if r:
        user_id, payment_ref, amount = r
        if payment_ref and payment_ref.startswith("points:"):
            try:
                pts = int(payment_ref.split(":")[1])
                change_points(user_id, pts)  # refund
            except:
                pass
        update_purchase_status(pid, "rejected", admin_note=admin_note)
        try:
            socketio.emit('purchase_rejected', {'pid': pid, 'uid': user_id, 'amount': amount}, room=f"user_{user_id}")
        except:
            pass
    conn.close()
    return redirect(url_for("admin_purchases"))

@app.route("/admin/withdraws")
@admin_required
def admin_withdraws():
    rows = list_withdraws()
    return render_template_string(ADMIN_WITHDRAWS_TEMPLATE, withdraws=rows)

@app.route("/admin/withdraw/<int:wid>/approve", methods=["POST"])
@admin_required
def admin_withdraw_approve(wid):
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT user_id, amount_toman FROM withdraws WHERE id=?", (wid,))
    r = c.fetchone()
    if r:
        user_id, amount = r
        update_withdraw_status(wid, "approved")
        try:
            socketio.emit('withdraw_approved', {'wid': wid, 'uid': user_id, 'amount': amount}, room=f"user_{user_id}")
        except:
            pass
    conn.close()
    return redirect(url_for("admin_withdraws"))

@app.route("/admin/withdraw/<int:wid>/reject", methods=["POST"])
@admin_required
def admin_withdraw_reject(wid):
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT user_id, points FROM withdraws WHERE id=?", (wid,))
    r = c.fetchone()
    if r:
        user_id, points = r
        change_points(user_id, points)
    update_withdraw_status(wid, "rejected")
    if r:
        try:
            socketio.emit('withdraw_rejected', {'wid': wid, 'uid': user_id}, room=f"user_{user_id}")
        except:
            pass
    conn.close()
    return redirect(url_for("admin_withdraws"))

@app.route("/admin/ads")
@admin_required
def admin_ads():
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT id, purchase_id, user_id, title, channel_link, description, image, phone, status, created_at, approved_at FROM ads ORDER BY created_at DESC")
    ads = c.fetchall(); conn.close()
    return render_template_string(ADMIN_ADS_TEMPLATE, ads=ads)

@app.route("/admin/ad/<int:aid>/toggle", methods=["POST"])
@admin_required
def admin_ad_toggle(aid):
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT status FROM ads WHERE id=?", (aid,))
    r = c.fetchone()
    if r:
        new = 'inactive' if r[0]=='active' else 'active'
        c.execute("UPDATE ads SET status=? WHERE id=?", (new, aid))
        conn.commit()
    conn.close()
    return redirect(url_for("admin_ads"))

# ---------- Admin: Settings page ----------
@app.route("/admin/settings", methods=["GET","POST"])
@admin_required
def admin_settings():
    global POINTS_PER_CLIP, POINTS_PER_LINK, POINTS_PER_JOIN, MIN_WITHDRAW_POINTS
    global AFFILIATE_SHARE, ONLINE_SECONDS, TOMAN_PER_POINT, BIG_ACTION_LABEL, REYMIT_BASE
    global VIP_PACKS, AD_PROMO_PACKS, AD_JOIN_PACKS
    if flask_request.method == "POST":
        try:
            POINTS_PER_CLIP = int(flask_request.form.get("POINTS_PER_CLIP", POINTS_PER_CLIP))
            set_config("POINTS_PER_CLIP", POINTS_PER_CLIP)
            POINTS_PER_LINK = int(flask_request.form.get("POINTS_PER_LINK", POINTS_PER_LINK))
            set_config("POINTS_PER_LINK", POINTS_PER_LINK)
            POINTS_PER_JOIN = int(flask_request.form.get("POINTS_PER_JOIN", POINTS_PER_JOIN))
            set_config("POINTS_PER_JOIN", POINTS_PER_JOIN)
            MIN_WITHDRAW_POINTS = int(flask_request.form.get("MIN_WITHDRAW_POINTS", MIN_WITHDRAW_POINTS))
            set_config("MIN_WITHDRAW_POINTS", MIN_WITHDRAW_POINTS)
            AFFILIATE_SHARE = float(flask_request.form.get("AFFILIATE_SHARE", AFFILIATE_SHARE))
            set_config("AFFILIATE_SHARE", AFFILIATE_SHARE)
            ONLINE_SECONDS = int(flask_request.form.get("ONLINE_SECONDS", ONLINE_SECONDS))
            set_config("ONLINE_SECONDS", ONLINE_SECONDS)
            TOMAN_PER_POINT = int(flask_request.form.get("TOMAN_PER_POINT", TOMAN_PER_POINT))
            set_config("TOMAN_PER_POINT", TOMAN_PER_POINT)
            BIG_ACTION_LABEL = flask_request.form.get("BIG_ACTION_LABEL", BIG_ACTION_LABEL)
            set_config("BIG_ACTION_LABEL", BIG_ACTION_LABEL)
            REYMIT_BASE = flask_request.form.get("REYMIT_BASE", REYMIT_BASE)
            set_config("REYMIT_BASE", REYMIT_BASE)
            # packs: JSON textareas
            vip_json = flask_request.form.get("VIP_PACKS_JSON", json.dumps(VIP_PACKS, ensure_ascii=False))
            ad_json = flask_request.form.get("AD_PROMO_PACKS_JSON", json.dumps(AD_PROMO_PACKS, ensure_ascii=False))
            aj_json = flask_request.form.get("AD_JOIN_PACKS_JSON", json.dumps(AD_JOIN_PACKS, ensure_ascii=False))
            VIP_PACKS = json.loads(vip_json)
            AD_PROMO_PACKS = json.loads(ad_json)
            AD_JOIN_PACKS = json.loads(aj_json)
            set_config("VIP_PACKS", VIP_PACKS)
            set_config("AD_PROMO_PACKS", AD_PROMO_PACKS)
            set_config("AD_JOIN_PACKS", AD_JOIN_PACKS)
            set_config("LAST_CONFIG_UPDATE", int(time.time()))
            log.info("Admin updated settings")
            return render_template_string("<p>تنظیمات ذخیره شد.</p><p><a href='/admin'>بازگشت</a></p>")
        except Exception as e:
            log.exception("Failed to save settings: %s", e)
            return render_template_string("<p>خطا در ذخیره تنظیمات: {{ err }}</p><p><a href='/admin/settings'>بازگشت</a></p>", err=str(e)), 400
    # GET: show form with current settings
    return render_template_string("""
<!doctype html><html lang="fa"><head><meta charset="utf-8"><title>ویرایش تنظیمات</title>""" + BASE_STYLES + """</head><body>
<div class="container" style="margin-top:18px">
  <div class="card"><h2>✏️ ویرایش تنظیمات ربات</h2>
    <form method="post" style="display:flex;flex-direction:column;gap:8px">
      <label>POINTS_PER_CLIP <input name="POINTS_PER_CLIP" class="input" value="{{ points_clip }}"></label>
      <label>POINTS_PER_LINK <input name="POINTS_PER_LINK" class="input" value="{{ points_link }}"></label>
      <label>POINTS_PER_JOIN <input name="POINTS_PER_JOIN" class="input" value="{{ points_join }}"></label>
      <label>MIN_WITHDRAW_POINTS <input name="MIN_WITHDRAW_POINTS" class="input" value="{{ min_withdraw }}"></label>
      <label>TOMAN_PER_POINT <input name="TOMAN_PER_POINT" class="input" value="{{ toman_per_point }}"></label>
      <label>AFFILIATE_SHARE <input name="AFFILIATE_SHARE" class="input" value="{{ affiliate_share }}"></label>
      <label>ONLINE_SECONDS <input name="ONLINE_SECONDS" class="input" value="{{ online_seconds }}"></label>
      <label>REYMIT_BASE <input name="REYMIT_BASE" class="input" value="{{ reymit }}"></label>
      <label>BIG_ACTION_LABEL <input name="BIG_ACTION_LABEL" class="input" value="{{ big_action }}"></label>
      <label>VIP_PACKS (JSON)<textarea name="VIP_PACKS_JSON" class="input" rows="6">{{ vip_json }}</textarea></label>
      <label>AD_PROMO_PACKS (JSON)<textarea name="AD_PROMO_PACKS_JSON" class="input" rows="6">{{ ad_json }}</textarea></label>
      <label>AD_JOIN_PACKS (JSON)<textarea name="AD_JOIN_PACKS_JSON" class="input" rows="6">{{ aj_json }}</textarea></label>
      <div style="display:flex;gap:8px">
        <button class="btn" type="submit">ذخیره</button>
        <a class="btn secondary" href="/admin">انصراف</a>
      </div>
    </form>
  </div>
</div>
</body></html>
""",
    points_clip=POINTS_PER_CLIP,
    points_link=POINTS_PER_LINK,
    points_join=POINTS_PER_JOIN,
    min_withdraw=MIN_WITHDRAW_POINTS,
    toman_per_point=TOMAN_PER_POINT,
    affiliate_share=AFFILIATE_SHARE,
    online_seconds=ONLINE_SECONDS,
    reymit=REYMIT_BASE,
    big_action=BIG_ACTION_LABEL,
    vip_json=json.dumps(VIP_PACKS, ensure_ascii=False, indent=2),
    ad_json=json.dumps(AD_PROMO_PACKS, ensure_ascii=False, indent=2),
    aj_json=json.dumps(AD_JOIN_PACKS, ensure_ascii=False, indent=2)
)

# ---------- Admin: Stats Dashboard ----------
def ts_range_days(days):
    now = int(time.time())
    start = now - days * 24 * 3600
    return start, now

def sql_count_purchases_between(start_ts, end_ts):
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM purchases WHERE created_at BETWEEN ? AND ?", (start_ts, end_ts))
    r = c.fetchone(); conn.close()
    return r or (0,0)

def sql_count_withdraws_between(start_ts, end_ts):
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*), COALESCE(SUM(amount_toman),0) FROM withdraws WHERE created_at BETWEEN ? AND ?", (start_ts, end_ts))
    r = c.fetchone(); conn.close()
    return r or (0,0)

def sql_ad_events_grouped(ad_kind, days=30):
    # returns list of (date_str, count) for last 'days' days
    conn = _get_conn(); c = conn.cursor()
    since = int(time.time()) - days*24*3600
    c.execute("""
        SELECT DATE(datetime(created_at, 'unixepoch'), 'localtime') as day, COUNT(*) FROM ad_events
        WHERE kind=? AND created_at >= ? GROUP BY day ORDER BY day ASC
    """, (ad_kind, since))
    rows = c.fetchall(); conn.close()
    return rows

def sql_top_ads(limit=10):
    conn = _get_conn(); c = conn.cursor()
    c.execute("""
      SELECT a.id, a.title, COALESCE(SUM(CASE WHEN e.kind='impression' THEN 1 ELSE 0 END),0) as imps,
             COALESCE(SUM(CASE WHEN e.kind='click' THEN 1 ELSE 0 END),0) as clicks
      FROM ads a LEFT JOIN ad_events e ON a.id = e.ad_id
      GROUP BY a.id ORDER BY imps DESC LIMIT ?
    """, (limit,))
    rows = c.fetchall(); conn.close()
    return rows

@app.route("/admin/stats")
@admin_required
def admin_stats():
    # compute basic stats: total users, online users, purchases and withdraws day/week/month, ad impressions & clicks charts
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users"); total_users = c.fetchone()[0]
    now = int(time.time())
    c.execute("SELECT COUNT(*) FROM users WHERE last_active >= ?", (now - ONLINE_SECONDS,))
    online_users = c.fetchone()[0]
    conn.close()
    # purchases & withdraws
    today_start = int(datetime.combine(datetime.utcnow().date(), datetime.min.time()).timestamp())
    week_start = int((datetime.utcnow() - timedelta(days=7)).timestamp())
    month_start = int((datetime.utcnow() - timedelta(days=30)).timestamp())
    purchases_today = sql_count_purchases_between(today_start, int(time.time()))
    purchases_week = sql_count_purchases_between(week_start, int(time.time()))
    purchases_month = sql_count_purchases_between(month_start, int(time.time()))
    withdraws_today = sql_count_withdraws_between(today_start, int(time.time()))
    withdraws_week = sql_count_withdraws_between(week_start, int(time.time()))
    withdraws_month = sql_count_withdraws_between(month_start, int(time.time()))
    # ad events (last 30 days) for chart
    imps = sql_ad_events_grouped('impression', days=30)
    clicks = sql_ad_events_grouped('click', days=30)
    # build date->count dict
    def build_series(rows):
        d = {}
        for day, cnt in rows:
            d[day] = cnt
        return d
    imps_map = build_series(imps)
    clicks_map = build_series(clicks)
    # create labels for last 30 days
    labels = []
    data_imps = []
    data_clicks = []
    for i in range(29, -1, -1):
        dt = datetime.utcnow().date() - timedelta(days=i)
        key = dt.isoformat()
        labels.append(key)
        data_imps.append(imps_map.get(key, 0))
        data_clicks.append(clicks_map.get(key, 0))
    top_ads = sql_top_ads(limit=10)
    return render_template_string("""
<!doctype html><html lang="fa"><head><meta charset="utf-8"><title>آمار و گزارش‌ها</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
""" + BASE_STYLES + """</head><body>
<div class="container" style="margin-top:18px">
  <div class="card"><h2>📊 آمار کلی</h2>
    <div class="small">کاربران کل: <strong>{{ total_users }}</strong> — آنلاین: <strong>{{ online_users }}</strong></div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px">
      <div class="pack-card">
        <div style="font-weight:800">خریدها — امروز</div>
        <div class="small">تعداد: {{ purchases_today[0] }} — مجموع: {{ purchases_today[1] }} تومان</div>
      </div>
      <div class="pack-card">
        <div style="font-weight:800">خریدها — هفته</div>
        <div class="small">تعداد: {{ purchases_week[0] }} — مجموع: {{ purchases_week[1] }} تومان</div>
      </div>
      <div class="pack-card">
        <div style="font-weight:800">خریدها — ماه</div>
        <div class="small">تعداد: {{ purchases_month[0] }} — مجموع: {{ purchases_month[1] }} تومان</div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px">
      <div class="pack-card">
        <div style="font-weight:800">برداشت‌ها — امروز</div>
        <div class="small">تعداد: {{ withdraws_today[0] }} — مجموع: {{ withdraws_today[1] }} تومان</div>
      </div>
      <div class="pack-card">
        <div style="font-weight:800">برداشت‌ها — هفته</div>
        <div class="small">تعداد: {{ withdraws_week[0] }} — مجموع: {{ withdraws_week[1] }} تومان</div>
      </div>
      <div class="pack-card">
        <div style="font-weight:800">برداشت‌ها — ماه</div>
        <div class="small">تعداد: {{ withdraws_month[0] }} — مجموع: {{ withdraws_month[1] }} تومان</div>
      </div>
    </div>

    <div style="margin-top:18px">
      <h3>نمودار نمایش‌ها و کلیک‌ها (۳۰ روز اخیر)</h3>
      <canvas id="impressionChart" height="120"></canvas>
    </div>

    <div style="margin-top:18px">
      <h3>پربازدیدترین آگهی‌ها (Top 10)</h3>
      <div class="table-responsive">
        <table class="table"><thead><tr><th>ID</th><th>عنوان</th><th>Impressions</th><th>Clicks</th></tr></thead><tbody>
        {% for a in top_ads %}
          <tr><td>{{ a[0] }}</td><td class="small">{{ a[1] }}</td><td>{{ a[2] }}</td><td>{{ a[3] }}</td></tr>
        {% endfor %}
        </tbody></table>
      </div>
    </div>

    <div style="margin-top:12px"><a class="btn secondary" href="/admin">بازگشت</a></div>
  </div>
</div>

<script>
const labels = {{ labels|tojson }};
const dataImps = {{ imps_data|tojson }};
const dataClicks = {{ clicks_data|tojson }};
const ctx = document.getElementById('impressionChart').getContext('2d');
new Chart(ctx, {
    type: 'line',
    data: {
        labels: labels,
        datasets: [
          { label: 'Impressions', data: dataImps, borderColor: '#00d4ff', backgroundColor: 'rgba(0,212,255,0.08)', fill:true },
          { label: 'Clicks', data: dataClicks, borderColor: '#ff9a00', backgroundColor: 'rgba(255,154,0,0.06)', fill:true }
        ]
    },
    options: {
        responsive: true,
        scales: { x: { display: true }, y: { beginAtZero: true } }
    }
});
</script>
</body></html>
""",
    total_users=total_users,
    online_users=online_users,
    purchases_today=purchases_today,
    purchases_week=purchases_week,
    purchases_month=purchases_month,
    withdraws_today=withdraws_today,
    withdraws_week=withdraws_week,
    withdraws_month=withdraws_month,
    labels=labels,
    imps_data=data_imps,
    clicks_data=data_clicks,
    top_ads=top_ads
)

# ---------- Admin: promo codes (create + redeem) ----------
@app.route("/admin/promo/create", methods=["GET","POST"])
@admin_required
def admin_promo_create():
    if flask_request.method == "POST":
        code = flask_request.form.get("code") or secrets.token_hex(6)
        points = int(flask_request.form.get("points", "0"))
        expires_days = int(flask_request.form.get("expires_days", "0"))
        expires_at = int(time.time()) + expires_days*24*3600 if expires_days>0 else None
        conn = _get_conn(); c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO promo_codes (code, points, created_by, expires_at, created_at) VALUES (?,?,?,?,?)",
                  (code, points, session.get('admin_id', 0), expires_at, int(time.time())))
        conn.commit(); conn.close()
        return render_template_string("<p>کد ایجاد شد: {{ code }}</p><p><a href='/admin'>بازگشت</a></p>", code=code)
    return render_template_string("""<!doctype html><html lang="fa"><head><meta charset="utf-8"><title>ایجاد کد هدیه</title>""" + BASE_STYLES + """</head><body>
    <div class="container"><div class="card"><h2>🎁 ایجاد کد هدیه</h2>
    <form method="post"><input name="code" class="input" placeholder="کد (اختیاری)"><input name="points" class="input" placeholder="امتیاز"><input name="expires_days" class="input" placeholder="انقضا (روز)"><div style="margin-top:8px"><button class="btn" type="submit">ایجاد</button></div></form>
    </div></div></body></html>""")

@app.route("/promo/redeem", methods=["POST"])
def promo_redeem():
    data = flask_request.get_json(force=True, silent=True) or {}
    uid = data.get("uid"); code = data.get("code")
    if not uid or not code:
        return jsonify({"ok": False, "error": "uid and code required"}), 400
    try: uid_i = int(uid)
    except: return jsonify({"ok": False, "error": "invalid uid"}), 400
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT code, points, used_by, expires_at FROM promo_codes WHERE code=?", (code,))
    r = c.fetchone()
    if not r:
        conn.close(); return jsonify({"ok": False, "error": "invalid code"}), 404
    _, points, used_by, expires_at = r
    if used_by:
        conn.close(); return jsonify({"ok": False, "error": "already used"}), 400
    if expires_at and int(time.time()) > expires_at:
        conn.close(); return jsonify({"ok": False, "error": "expired"}), 400
    c.execute("UPDATE promo_codes SET used_by=?, used_at=? WHERE code=?", (uid_i, int(time.time()), code))
    conn.commit(); conn.close()
    change_points(uid_i, points)
    log_earning_event(uid_i, "promo_redeem", points)
    return jsonify({"ok": True, "points": points})

# ---------- Ad view / click endpoints (register events) ----------
@app.route("/ad/view/<int:aid>")
def ad_view(aid):
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT id, user_id, title, channel_link, description, image, status FROM ads WHERE id=?", (aid,))
    ad = c.fetchone(); conn.close()
    if not ad or ad[6] != 'active':
        return "Ad not found", 404
    # record impression
    try:
        conn = _get_conn(); c = conn.cursor()
        c.execute("INSERT INTO ad_events (ad_id, user_id, kind, created_at) VALUES (?,?,?,?)", (aid, None, "impression", int(time.time())))
        conn.commit(); conn.close()
    except Exception:
        log.exception("Failed to record impression for ad %s", aid)
    title = ad[2]; channel = ad[3]; desc = ad[4]; image = ad[5]
    click_url = url_for("ad_click_redirect", aid=aid)
    tts_button = ""
    if TTS_AVAILABLE:
        tts_button = f'<a class="btn secondary" href="/ad/tts/{aid}" target="_blank">🔊 پخش صوتی</a>'
    img_html = f"<img src='/uploads/{image}' style='max-width:100%;border-radius:8px'/>" if image else ""
    html = f"""<!doctype html><html lang="fa"><head><meta charset="utf-8"><title>{title}</title>""" + BASE_STYLES + """</head><body>
    <div class="container"><div class="card"><h2>{title}</h2><p class="small">{desc or ''}</p>{img_html}
    <div style="margin-top:12px"><a class="btn" href="{click_url}" target="_blank">رفتن به کانال / کلیک</a>{tts_button}</div>
    </div></div></body></html>
    """
    return html

@app.route("/ad/click/<int:aid>")
def ad_click_redirect(aid):
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT channel_link FROM ads WHERE id=?", (aid,))
    r = c.fetchone(); conn.close()
    if not r:
        return "Ad not found", 404
    channel_link = r[0]
    # record click
    try:
        conn = _get_conn(); c = conn.cursor()
        c.execute("INSERT INTO ad_events (ad_id, user_id, kind, created_at) VALUES (?,?,?,?)", (aid, None, "click", int(time.time())))
        conn.commit(); conn.close()
    except Exception:
        log.exception("Failed to record click for ad %s", aid)
    return redirect(channel_link)

@app.route("/ad/tts/<int:aid>")
def ad_tts(aid):
    if not TTS_AVAILABLE:
        return "TTS not available on server (install gTTS)", 501
    conn = _get_conn(); c = conn.cursor()
    c.execute("SELECT title, description FROM ads WHERE id=? AND status='active'", (aid,))
    r = c.fetchone(); conn.close()
    if not r:
        return "Ad not found", 404
    title, desc = r
    text = f"{title}. {desc or ''}"
    filename = f"ad_{aid}_tts_{abs(hash(text))%100000}.mp3"
    path = os.path.join(UPLOAD_FOLDER, "tts", filename)
    if not os.path.exists(path):
        try:
            tts = gTTS(text=text, lang='fa')
            tts.save(path)
        except Exception as e:
            log.exception("gTTS failed: %s", e)
            return "TTS generation failed", 500
    return redirect(url_for('uploaded_tts', filename=filename))

# ---------- Guide & Support ----------
@app.route("/page/guide")
def page_guide():
    return render_template_string(GUIDE_TEMPLATE)

@app.route("/page/support")
def page_support():
    return render_template_string(SUPPORT_TEMPLATE)

# ---------- SocketIO handlers ----------
@socketio.on('join')
def on_join(data):
    uid = data.get('uid')
    try:
        uid_i = int(uid)
    except:
        return
    room = f"user_{uid_i}"
    join_room(room)
    emit('joined', {'room': room})

# ---------- Run ----------
if __name__ == "__main__":
    log.info("Starting mobile-first full app (VIP + Ads) on %s:%s", APP_HOST, APP_PORT)
    # If you run into socket issues on Android, ensure eventlet is installed: pip install eventlet
    socketio.run(app, host=APP_HOST, port=APP_PORT, debug=os.environ.get("FLASK_DEBUG") == "1")
