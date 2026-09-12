# SmartLink - Multi-user URL Shortener
# Brand: Shuvo Ahmed | Support: https://t.me/Ahmed_shuvo_786

import os
import random
import string
import hashlib
import html
import json
import sqlite3
import urllib.request
from functools import wraps
from pathlib import Path

from flask import Flask, request, redirect, jsonify, abort, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "smartlink-local-secret-change-me-please")
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 30  # 30 days

# Postgres on Render (persistent) OR SQLite locally
# Accept common Render / alternate names
DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("POSTGRES_URL")
    or os.getenv("POSTGRES_CONNECTION_STRING")
    or ""
).strip().strip('"').strip("'")
USE_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
DB_PATH = Path(__file__).resolve().parent / "smartlink.db"
ADMIN_EMAIL = (os.getenv("ADMIN_EMAIL") or "").strip().lower()

if USE_PG:
    import psycopg
    from psycopg.rows import dict_row

SUPPORT_NAME = "Shuvo Ahmed"
SUPPORT_HANDLE = "@Ahmed_shuvo_786"
SUPPORT_URL = "https://t.me/Ahmed_shuvo_786"

COUNTRY_NAMES = {
    "AF": "Afghanistan", "AL": "Albania", "DZ": "Algeria", "AR": "Argentina",
    "AU": "Australia", "AT": "Austria", "BD": "Bangladesh", "BE": "Belgium",
    "BR": "Brazil", "BG": "Bulgaria", "KH": "Cambodia", "CA": "Canada",
    "CL": "Chile", "CN": "China", "CO": "Colombia", "HR": "Croatia",
    "CZ": "Czechia", "DK": "Denmark", "EG": "Egypt", "EE": "Estonia",
    "ET": "Ethiopia", "FI": "Finland", "FR": "France", "DE": "Germany",
    "GH": "Ghana", "GR": "Greece", "HK": "Hong Kong", "HU": "Hungary",
    "IN": "India", "ID": "Indonesia", "IR": "Iran", "IQ": "Iraq",
    "IE": "Ireland", "IL": "Israel", "IT": "Italy", "JP": "Japan",
    "JO": "Jordan", "KE": "Kenya", "KW": "Kuwait", "LV": "Latvia",
    "LB": "Lebanon", "LY": "Libya", "LT": "Lithuania", "MY": "Malaysia",
    "MV": "Maldives", "MX": "Mexico", "MA": "Morocco", "MM": "Myanmar",
    "NP": "Nepal", "NL": "Netherlands", "NZ": "New Zealand", "NG": "Nigeria",
    "NO": "Norway", "OM": "Oman", "PK": "Pakistan", "PS": "Palestine",
    "PH": "Philippines", "PL": "Poland", "PT": "Portugal", "QA": "Qatar",
    "RO": "Romania", "RU": "Russia", "SA": "Saudi Arabia", "RS": "Serbia",
    "SG": "Singapore", "SK": "Slovakia", "ZA": "South Africa", "KR": "South Korea",
    "ES": "Spain", "LK": "Sri Lanka", "SE": "Sweden", "CH": "Switzerland",
    "TW": "Taiwan", "TH": "Thailand", "TR": "Turkey", "UA": "Ukraine",
    "AE": "United Arab Emirates", "GB": "United Kingdom", "UK": "United Kingdom",
    "US": "United States", "VN": "Vietnam", "YE": "Yemen",
}

BOT_HINTS = (
    "facebookexternalhit", "facebot", "meta-externalagent", "meta-externalfetcher",
    "twitterbot", "linkedinbot", "slackbot", "whatsapp", "telegrambot", "discordbot",
    "googlebot", "bingbot", "applebot", "semrush", "ahrefs", "preview",
    "bot", "spider", "crawl", "slurp", "uptimerobot", "pingdom", "headless",
    "phantomjs", "selenium", "httpclient", "python-requests", "curl/", "wget/",
    "scrapy", "bytespider", "yandexbot", "baiduspider", "duckduckbot",
    "embedly", "quora link preview", "pinterest", "redditbot", "vkshare",
    "skypeuripreview", "nuzzel", "outbrain", "yahoo! slurp", "ia_archiver",
)


class _Cursor:
    """Translate ? placeholders for Postgres; keep SQLite as-is."""
    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=None):
        if USE_PG:
            sql = sql.replace("?", "%s")
        if params is None:
            return self._cur.execute(sql)
        return self._cur.execute(sql, params)

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


class _Conn:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return _Cursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._conn.close()


def get_db():
    if USE_PG:
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if "sslmode=" not in url:
            url = url + ("&" if "?" in url else "?") + "sslmode=require"
        conn = psycopg.connect(url, connect_timeout=15)
        return _Conn(conn)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return _Conn(conn)


def setup_database():
    with get_db() as conn:
        cur = conn.cursor()
        if USE_PG:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                    is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS links (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
                    short_code TEXT UNIQUE NOT NULL,
                    original_url TEXT NOT NULL,
                    clicks BIGINT NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS click_logs (
                    id BIGSERIAL PRIMARY KEY,
                    link_id BIGINT REFERENCES links(id) ON DELETE CASCADE,
                    short_code TEXT NOT NULL,
                    country TEXT DEFAULT 'Unknown',
                    device TEXT DEFAULT 'Others',
                    operating_system TEXT DEFAULT 'Others',
                    browser TEXT DEFAULT 'Others',
                    ip_hash TEXT,
                    clicked_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            # Safe migrations
            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='public' AND table_name='users' AND column_name='is_blocked'
            """)
            if not cur.fetchone():
                cur.execute("ALTER TABLE users ADD COLUMN is_blocked BOOLEAN NOT NULL DEFAULT FALSE")
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    is_blocked INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            # Migration for existing DBs without is_blocked
            real = conn._conn
            info = real.execute("PRAGMA table_info(users)").fetchall()
            cols = [row[1] for row in info]
            if "is_blocked" not in cols:
                cur.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER NOT NULL DEFAULT 0")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    short_code TEXT UNIQUE NOT NULL,
                    original_url TEXT NOT NULL,
                    clicks INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS click_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    link_id INTEGER REFERENCES links(id) ON DELETE CASCADE,
                    short_code TEXT NOT NULL,
                    country TEXT DEFAULT 'Unknown',
                    device TEXT DEFAULT 'Others',
                    operating_system TEXT DEFAULT 'Others',
                    browser TEXT DEFAULT 'Others',
                    ip_hash TEXT,
                    clicked_at TEXT DEFAULT (datetime('now'))
                )
            """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_links_user_id ON links(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_links_short_code ON links(short_code)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_click_logs_link_id ON click_logs(link_id)")
        conn.commit()


def get_client_ip():
    for header in ("CF-Connecting-IP", "True-Client-IP", "X-Real-IP", "X-Forwarded-For", "X-Client-IP"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.remote_addr or ""


def is_bot(user_agent):
    ua = (user_agent or "").lower()
    return any(h in ua for h in BOT_HINTS)


def _fetch_json(url, timeout=1.5):
    req = urllib.request.Request(url, headers={"User-Agent": "SmartLink/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_country(ip, user_agent=""):
    try:
        header_code = (
            request.headers.get("CF-IPCountry")
            or request.headers.get("CloudFront-Viewer-Country")
            or request.headers.get("X-AppEngine-Country")
        )
        if header_code:
            code = header_code.strip().upper()
            if code and code not in ("XX", "T1", "ZZ"):
                return COUNTRY_NAMES.get(code, code)
        if not ip or ip in ("127.0.0.1", "::1"):
            return "Local"
        if is_bot(user_agent):
            return "Bot / Preview"
        lookups = [
            ("https://ipwho.is/" + ip, lambda d: d.get("country") if d.get("success") else None),
            ("https://ipapi.co/" + ip + "/json/", lambda d: None if d.get("error") else d.get("country_name")),
            ("http://ip-api.com/json/" + ip + "?fields=status,country",
             lambda d: d.get("country") if d.get("status") == "success" else None),
        ]
        for url, pick in lookups:
            try:
                data = _fetch_json(url)
                name = pick(data)
                if name:
                    return str(name)
            except Exception:
                continue
        return "Unknown"
    except Exception:
        return "Unknown"


def get_device(user_agent):
    ua = (user_agent or "").lower()
    if is_bot(user_agent):
        return "Bot / Preview"
    if "ipad" in ua:
        return "iPad"
    if "iphone" in ua:
        return "iPhone"
    if "android" in ua and "mobile" in ua:
        return "Android"
    if "android" in ua:
        return "Android Tablet"
    if "windows" in ua:
        return "Windows PC"
    if "macintosh" in ua or "mac os" in ua:
        return "Mac"
    if "linux" in ua:
        return "Linux"
    return "Others"


def get_operating_system(user_agent):
    ua = (user_agent or "").lower()
    if is_bot(user_agent):
        return "Bot"
    if "iphone" in ua or "ipad" in ua:
        return "iOS"
    if "android" in ua:
        return "Android"
    if "windows" in ua:
        return "Windows"
    if "mac os" in ua or "macintosh" in ua:
        return "macOS"
    if "linux" in ua:
        return "Linux"
    return "Others"


def get_browser(user_agent):
    ua = (user_agent or "").lower()
    if is_bot(user_agent):
        return "Bot / Crawler"
    if "edg/" in ua:
        return "Microsoft Edge"
    if "opr/" in ua or "opera" in ua:
        return "Opera"
    if "firefox/" in ua or "fxios" in ua:
        return "Mozilla Firefox"
    if "samsungbrowser" in ua:
        return "Samsung Internet"
    if "chrome/" in ua or "crios" in ua:
        return "Google Chrome"
    if "safari/" in ua and "chrome" not in ua:
        return "Safari"
    return "Others"


def hash_ip(ip):
    return hashlib.sha256((ip or "").encode("utf-8")).hexdigest()


def format_dt(dt):
    if not dt:
        return "—"
    try:
        if isinstance(dt, str):
            # SQLite datetime strings: "2026-09-01 12:30:00"
            from datetime import datetime as _dt
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
                try:
                    dt = _dt.strptime(dt[:26].replace("T", " "), fmt if "%f" not in fmt else "%Y-%m-%d %H:%M:%S")
                    break
                except ValueError:
                    continue
        return dt.strftime("%d %b %Y  ·  %I:%M %p")
    except Exception:
        return str(dt)


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, name, email, is_admin, CASE WHEN is_blocked THEN 1 ELSE 0 END FROM users WHERE id = ?",
                (uid,),
            )
            row = cur.fetchone()
        if not row:
            session.clear()
            return None
        if row[4]:
            session.clear()
            return None
        return {
            "id": row[0],
            "name": row[1],
            "email": row[2],
            "is_admin": bool(row[3]),
            "is_blocked": bool(row[4]),
        }
    except Exception as e:
        print("current_user error:", e)
        return None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("signin", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("signin", next=request.path))
        if not user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


CSS = """
:root {
  --ink: #08090c; --panel: #12141a; --raised: #1b1f28; --line: #2c313c;
  --gold: #d4af37; --ivory: #f4efe4; --muted: #9a9386; --danger: #f87171;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { min-height: 100%; }
body {
  font-family: Outfit, system-ui, sans-serif;
  background: radial-gradient(ellipse at top, rgba(212,175,55,.08), transparent 55%), var(--ink);
  color: var(--ivory);
}
a { color: inherit; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 0 20px; }
header {
  border-bottom: 1px solid var(--line); position: sticky; top: 0; z-index: 20;
  background: rgba(8,9,12,.88); backdrop-filter: blur(12px);
}
.nav { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 16px 0; flex-wrap: wrap; }
.brand { display: flex; align-items: center; gap: 12px; text-decoration: none; }
.mark {
  width: 36px; height: 36px; border: 1px solid rgba(212,175,55,.4);
  border-radius: 8px; display: grid; place-items: center; color: var(--gold); font-size: 14px;
}
.brand b { display: block; font-family: "Cormorant Garamond", serif; font-size: 20px; font-weight: 600; }
.brand small { display: block; letter-spacing: .22em; text-transform: uppercase; font-size: 10px; color: var(--muted); }
.menu { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.menu a, .menu button {
  background: none; border: 0; color: var(--muted); padding: 10px 12px;
  border-radius: 8px; text-decoration: none; font-size: 14px; cursor: pointer;
}
.menu a.active, .menu a:hover, .menu button:hover { color: var(--ivory); background: var(--raised); }
.menu .gold { color: var(--gold); }
main { padding: 40px 0 72px; }
h1, h2, h3 { font-family: "Cormorant Garamond", serif; font-weight: 600; text-wrap: balance; }
.kicker { color: var(--gold); letter-spacing: .28em; text-transform: uppercase; font-size: 11px; }
.hero h1 { font-size: clamp(36px, 6vw, 58px); line-height: .95; margin: 12px 0 16px; }
.hero p { color: var(--muted); max-width: 520px; line-height: 1.6; }
form.row { display: flex; gap: 12px; margin-top: 32px; }
form.stack { display: flex; flex-direction: column; gap: 14px; margin-top: 24px; max-width: 420px; }
form.stack label { font-size: 12px; letter-spacing: .12em; text-transform: uppercase; color: var(--muted); display: block; margin-bottom: 6px; }
input[type=url], input[type=search], input[type=email], input[type=password], input[type=text], .search {
  width: 100%; min-height: 52px; border: 1px solid var(--line); background: var(--panel);
  color: var(--ivory); border-radius: 10px; padding: 0 16px; font: inherit; outline: none;
}
input:focus { border-color: var(--gold); }
.btn {
  min-height: 52px; border: 0; border-radius: 10px; background: var(--gold); color: var(--ink);
  font-weight: 700; padding: 0 22px; cursor: pointer; text-decoration: none; display: inline-flex;
  align-items: center; justify-content: center; white-space: nowrap;
}
.btn-ghost { background: var(--raised); color: var(--ivory); border: 1px solid var(--line); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 28px 0; }
.stat, .card, .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 16px; }
.stat { padding: 22px; }
.stat b { display: block; font-family: "Cormorant Garamond", serif; font-size: 36px; color: var(--gold); }
.stat span { color: var(--muted); font-size: 12px; letter-spacing: .16em; text-transform: uppercase; }
.card { overflow: hidden; }
.card-h { padding: 20px 22px; border-bottom: 1px solid var(--line); }
.card-h p { color: var(--muted); font-size: 14px; margin-top: 4px; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; min-width: 720px; }
th {
  text-align: left; font-size: 11px; letter-spacing: .14em; text-transform: uppercase;
  color: var(--muted); font-weight: 500; background: var(--raised); padding: 12px 18px;
}
td { padding: 14px 18px; border-top: 1px solid var(--line); font-size: 14px; vertical-align: middle; }
.gold { color: var(--gold); font-weight: 700; text-decoration: none; }
.muted { color: var(--muted); }
.err { color: var(--danger); font-size: 14px; margin-top: 10px; }
.tag {
  display: inline-block; background: var(--raised); color: var(--gold);
  padding: 4px 10px; border-radius: 6px; font-size: 13px; font-weight: 600;
}
.grid3 { display: grid; gap: 12px; grid-template-columns: 1fr; margin: 22px 0; }
.panel { padding: 20px; }
.chip {
  display: flex; justify-content: space-between; gap: 12px; padding: 8px 0;
  font-size: 14px; border-bottom: 1px solid var(--line);
}
.chip:last-child { border: 0; }
footer { border-top: 1px solid var(--line); padding: 22px 0; color: var(--muted); font-size: 14px; }
.foot { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.modal-bg {
  position: fixed; inset: 0; background: rgba(8,9,12,.82);
  display: none; place-items: center; padding: 16px; z-index: 50;
}
.modal-bg.open { display: grid; }
.modal {
  width: min(440px, 100%); background: var(--panel);
  border: 1px solid var(--line); border-radius: 16px; padding: 24px;
}
.tg {
  display: block; margin-top: 18px; padding: 18px;
  border: 1px solid rgba(212,175,55,.4); border-radius: 12px;
  text-decoration: none; background: var(--raised);
}
.tg b { display: block; font-family: "Cormorant Garamond", serif; font-size: 28px; }
.tg span { color: var(--gold); }
.auth-box { max-width: 460px; margin: 0 auto; }
@media (min-width: 800px) { .grid3 { grid-template-columns: 1fr 1fr 1fr; } }
@media (max-width: 700px) {
  form.row { flex-direction: column; }
  .btn { width: 100%; }
}
"""


def layout(title, body, active=""):
    user = current_user()

    def nav(href, key, label):
        cls = "active" if active == key else ""
        return f'<a class="{cls}" href="{href}">{label}</a>'

    if user:
        admin_link = nav("/admin", "admin", "Admin") if user["is_admin"] else ""
        auth_links = (
            nav("/", "home", "Create")
            + nav("/dashboard", "dashboard", "Dashboard")
            + nav("/live-console", "live", "Live")
            + admin_link
            + f'<span class="muted" style="padding:0 8px;font-size:13px">{html.escape(user["name"])}</span>'
            + '<a href="/signout">Sign out</a>'
        )
    else:
        auth_links = nav("/signin", "signin", "Sign in") + nav("/signup", "signup", "Sign up")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@600;700&family=Outfit:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<header>
  <div class="wrap nav">
    <a class="brand" href="/">
      <span class="mark">◆</span>
      <span><b>SmartLink</b><small>Shuvo Ahmed</small></span>
    </a>
    <nav class="menu">
      {auth_links}
      <button type="button" class="gold" onclick="openSupport()">Support</button>
    </nav>
  </div>
</header>
<main><div class="wrap">{body}</div></main>
<footer>
  <div class="wrap foot">
    <span>SmartLink · Shuvo Ahmed</span>
    <button type="button" class="gold" style="background:none;border:0;cursor:pointer;font:inherit;color:var(--gold)" onclick="openSupport()">Support · Telegram</button>
  </div>
</footer>
<div class="modal-bg" id="supportModal" onclick="if(event.target===this)closeSupport()">
  <div class="modal">
    <p class="kicker">Support</p>
    <h2 style="font-size:32px;margin:8px 0 10px">Direct line</h2>
    <p class="muted">Tap the name to open Telegram.</p>
    <a class="tg" href="{SUPPORT_URL}" target="_blank" rel="noopener">
      <b>{SUPPORT_NAME}</b>
      <span>{SUPPORT_HANDLE}</span>
      <p class="muted" style="margin-top:10px;letter-spacing:.16em;text-transform:uppercase;font-size:11px">Open Telegram →</p>
    </a>
    <p style="margin-top:14px">
      <button type="button" onclick="closeSupport()" style="background:none;border:0;color:var(--muted);cursor:pointer">Close</button>
    </p>
  </div>
</div>
<script>
function openSupport(){{ document.getElementById('supportModal').classList.add('open'); }}
function closeSupport(){{ document.getElementById('supportModal').classList.remove('open'); }}
</script>
</body>
</html>"""


# ---------- AUTH ----------

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user():
        return redirect("/")
    error = ""
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if len(name) < 2:
            error = "Name is too short."
        elif "@" not in email or "." not in email:
            error = "Enter a valid email."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        else:
            is_admin = bool(ADMIN_EMAIL and email == ADMIN_EMAIL)
            try:
                with get_db() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT id FROM users WHERE email = ?", (email,))
                    if cur.fetchone():
                        error = "Email already registered. Please sign in."
                    else:
                        cur.execute(
                            """
                            INSERT INTO users (name, email, password_hash, is_admin)
                            VALUES (?, ?, ?, ?) RETURNING id
                            """,
                            (name, email, generate_password_hash(password), is_admin),
                        )
                        uid = cur.fetchone()[0]
                        conn.commit()
                        session["user_id"] = uid
                        session.permanent = True
                        return redirect("/")
            except Exception as e:
                error = f"Signup failed: {e}"

    err_html = f'<p class="err">{html.escape(error)}</p>' if error else ""
    body = f"""
    <div class="auth-box">
      <p class="kicker">Account</p>
      <h1>Create your workspace</h1>
      <p class="muted" style="margin-top:8px">Your links stay private — only you can see them.</p>
      <form class="stack" method="POST" action="/signup">
        <div>
          <label>Name</label>
          <input type="text" name="name" required minlength="2" placeholder="Your name">
        </div>
        <div>
          <label>Email</label>
          <input type="email" name="email" required placeholder="you@email.com">
        </div>
        <div>
          <label>Password</label>
          <input type="password" name="password" required minlength="6" placeholder="Min 6 characters">
        </div>
        <button class="btn" type="submit">Sign up</button>
      </form>
      {err_html}
      <p class="muted" style="margin-top:18px">Already have an account? <a class="gold" href="/signin">Sign in</a></p>
    </div>
    """
    return layout("Sign up · SmartLink", body, "signup")


@app.route("/signin", methods=["GET", "POST"])
def signin():
    if current_user():
        return redirect("/")
    error = ""
    next_url = request.args.get("next") or request.form.get("next") or "/"
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        try:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, password_hash, CASE WHEN is_blocked THEN 1 ELSE 0 END FROM users WHERE email = ?",
                    (email,),
                )
                row = cur.fetchone()
            if not row or not check_password_hash(row[1], password):
                error = "Invalid email or password."
            elif int(row[2] or 0):
                error = "Your account has been blocked by admin. Contact support."
            else:
                session["user_id"] = row[0]
                session.permanent = True
                if not str(next_url).startswith("/"):
                    next_url = "/"
                return redirect(next_url)
        except Exception as e:
            error = f"Sign in failed: {e}"

    err_html = f'<p class="err">{html.escape(error)}</p>' if error else ""
    body = f"""
    <div class="auth-box">
      <p class="kicker">Welcome back</p>
      <h1>Sign in</h1>
      <form class="stack" method="POST" action="/signin">
        <input type="hidden" name="next" value="{html.escape(str(next_url))}">
        <div>
          <label>Email</label>
          <input type="email" name="email" required placeholder="you@email.com">
        </div>
        <div>
          <label>Password</label>
          <input type="password" name="password" required placeholder="Your password">
        </div>
        <button class="btn" type="submit">Sign in</button>
      </form>
      {err_html}
      <p class="muted" style="margin-top:18px">New here? <a class="gold" href="/signup">Create account</a></p>
    </div>
    """
    return layout("Sign in · SmartLink", body, "signin")


@app.route("/signout")
def signout():
    session.clear()
    return redirect("/signin")


# ---------- APP ----------

@app.route("/")
@login_required
def home():
    body = """
    <section class="hero">
      <p class="kicker">Your private links</p>
      <h1>Create a short link.</h1>
      <p>Only you can see the links you create. Clicks record country, device, OS, browser, date and time.</p>
      <form class="row" method="POST" action="/create">
        <input type="url" name="url" placeholder="https://example.com/your-page" required>
        <button class="btn" type="submit">Create link</button>
      </form>
    </section>
    """
    return layout("SmartLink", body, "home")


@app.route("/create", methods=["POST"])
@login_required
def create_link():
    user = current_user()
    if not user:
        return redirect(url_for("signin"))

    original_url = (request.form.get("url") or "").strip()
    if not original_url.startswith(("http://", "https://")):
        body = """
        <p class="kicker">Error</p>
        <h1>Invalid URL</h1>
        <p class="muted">URL must start with http:// or https://</p>
        <p style="margin-top:20px"><a class="btn" href="/">Try again</a></p>
        """
        return layout("Invalid URL", body, "home"), 400

    try:
        chars = string.ascii_letters + string.digits
        short_code = None
        with get_db() as conn:
            cur = conn.cursor()
            # Confirm user exists (foreign key)
            cur.execute("SELECT id FROM users WHERE id = ?", (user["id"],))
            if not cur.fetchone():
                session.clear()
                return redirect(url_for("signin"))

            for _ in range(25):
                code = "".join(random.choice(chars) for _ in range(7))
                cur.execute("SELECT 1 FROM links WHERE short_code = ?", (code,))
                if not cur.fetchone():
                    short_code = code
                    break
            if not short_code:
                raise RuntimeError("Could not generate a unique short code. Try again.")

            cur.execute(
                """
                INSERT INTO links (user_id, short_code, original_url)
                VALUES (?, ?, ?)
                """,
                (user["id"], short_code, original_url),
            )
            conn.commit()

        short_url = request.host_url.rstrip("/") + "/" + short_code
        safe_url = html.escape(short_url)
        body = f"""
        <p class="kicker">Ready</p>
        <h1>Link created</h1>
        <div class="card" style="margin-top:24px;padding:24px">
          <p class="muted">Your private SmartLink</p>
          <p style="margin:10px 0 12px;word-break:break-all">
            <a class="gold" id="newShortLink" href="{safe_url}">{safe_url}</a>
          </p>
          <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:center">
            <button type="button" class="btn" onclick="copyText('{safe_url}', this)">Copy link</button>
            <a class="btn btn-ghost" href="/dashboard">Open dashboard</a>
            <a class="btn btn-ghost" href="/">Create another</a>
          </div>
        </div>
        <script>
        function copyText(text, btn) {{
          navigator.clipboard.writeText(text).then(function() {{
            var old = btn.textContent;
            btn.textContent = "Copied!";
            setTimeout(function() {{ btn.textContent = old; }}, 1500);
          }}).catch(function() {{
            var ta = document.createElement("textarea");
            ta.value = text; document.body.appendChild(ta); ta.select();
            document.execCommand("copy"); document.body.removeChild(ta);
            var old = btn.textContent;
            btn.textContent = "Copied!";
            setTimeout(function() {{ btn.textContent = old; }}, 1500);
          }});
        }}
        </script>
        """
        return layout("Link created · SmartLink", body, "home")

    except Exception as e:
        print("CREATE ERROR:", repr(e))
        body = f"""
        <p class="kicker">Error</p>
        <h1>Could not create link</h1>
        <p class="err">{html.escape(str(e))}</p>
        <p class="muted" style="margin-top:12px">
          Make sure you are signed in and try again. If it keeps failing, delete smartlink.db and restart.
        </p>
        <p style="margin-top:20px"><a class="btn" href="/">Try again</a></p>
        """
        return layout("Create failed", body, "home"), 500


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT short_code, original_url, clicks, created_at
                FROM links WHERE user_id = ? ORDER BY id DESC
                """,
                (user["id"],),
            )
            links = cur.fetchall()
            cur.execute("SELECT COUNT(*) FROM links WHERE user_id = ?", (user["id"],))
            total_links = cur.fetchone()[0]
            cur.execute(
                "SELECT COALESCE(SUM(clicks), 0) FROM links WHERE user_id = ?",
                (user["id"],),
            )
            total_clicks = cur.fetchone()[0]
    except Exception as e:
        return f"Dashboard error: {html.escape(str(e))}", 500

    base = request.host_url.rstrip("/")
    rows = ""
    for short, url, clicks, created in links:
        safe_url = html.escape(url)
        display = safe_url if len(url) < 58 else html.escape(url[:55] + "...")
        sc = html.escape(short)
        full_short = html.escape(base + "/" + short)
        rows += f"""
        <tr>
          <td>
            <a class="gold" href="/{sc}">/{sc}</a>
            <button type="button" class="btn btn-ghost" style="padding:2px 8px;font-size:11px;margin-left:6px"
                    onclick="copyText('{full_short}', this)">Copy</button>
          </td>
          <td class="muted" title="{safe_url}">{display}</td>
          <td>{int(clicks)}</td>
          <td class="muted">{format_dt(created)}</td>
          <td>
            <a href="/analytics/{sc}">View</a>
            <form method="POST" action="/delete/{sc}" style="display:inline;margin-left:10px"
                  onsubmit="return confirm('Delete this link permanently?')">
              <button type="submit" class="btn btn-ghost" style="padding:4px 10px;font-size:12px;color:#f87171">Delete</button>
            </form>
          </td>
        </tr>
        """
    if not rows:
        rows = '<tr><td colspan="5" class="muted" style="text-align:center;padding:48px">No links yet.</td></tr>'

    body = f"""
    <p class="kicker">Only your data</p>
    <h1>Dashboard</h1>
    <div class="stats">
      <div class="stat"><b>{total_links}</b><span>Your links</span></div>
      <div class="stat"><b>{total_clicks}</b><span>Your clicks</span></div>
    </div>
    <div class="card">
      <div class="card-h">
        <h2>Your SmartLinks</h2>
        <p>Other users cannot see these links. You can delete your own links anytime.</p>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Short link</th><th>Destination</th><th>Clicks</th><th>Created</th><th>Actions</th></tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </div>
    """
    body += """
    <script>
    function copyText(text, btn) {
      navigator.clipboard.writeText(text).then(function() {
        var old = btn.textContent;
        btn.textContent = "Copied!";
        setTimeout(function() { btn.textContent = old; }, 1500);
      }).catch(function() {
        var ta = document.createElement("textarea");
        ta.value = text; document.body.appendChild(ta); ta.select();
        document.execCommand("copy"); document.body.removeChild(ta);
        var old = btn.textContent;
        btn.textContent = "Copied!";
        setTimeout(function() { btn.textContent = old; }, 1500);
      });
    }
    </script>
    """
    return layout("Dashboard · SmartLink", body, "dashboard")


@app.route("/analytics/<short_code>")
@login_required
def analytics(short_code):
    user = current_user()
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, short_code, original_url, clicks, created_at, user_id
                FROM links WHERE short_code = ?
                """,
                (short_code,),
            )
            link = cur.fetchone()
            if not link:
                abort(404)
            link_id, sc, original_url, clicks, created_at, owner_id = link
            if owner_id != user["id"] and not user["is_admin"]:
                abort(403)
            cur.execute(
                """
                SELECT country, device, operating_system, browser, clicked_at
                FROM click_logs WHERE link_id = ?
                ORDER BY id DESC LIMIT 200
                """,
                (link_id,),
            )
            click_rows = cur.fetchall()
            cur.execute(
                """
                SELECT country, COUNT(*) FROM click_logs
                WHERE link_id = ? GROUP BY country ORDER BY COUNT(*) DESC LIMIT 8
                """,
                (link_id,),
            )
            top_countries = cur.fetchall()
            cur.execute(
                """
                SELECT device, COUNT(*) FROM click_logs
                WHERE link_id = ? GROUP BY device ORDER BY COUNT(*) DESC LIMIT 8
                """,
                (link_id,),
            )
            top_devices = cur.fetchall()
            cur.execute(
                """
                SELECT browser, COUNT(*) FROM click_logs
                WHERE link_id = ? GROUP BY browser ORDER BY COUNT(*) DESC LIMIT 8
                """,
                (link_id,),
            )
            top_browsers = cur.fetchall()
    except Exception as e:
        code = getattr(e, "code", None)
        if code in (403, 404):
            raise
        return f"Analytics error: {html.escape(str(e))}", 500

    def chips(items):
        if not items:
            return '<p class="muted">No data yet</p>'
        out = ""
        for name, n in items:
            out += (
                f'<div class="chip"><span>{html.escape(str(name or "Unknown"))}</span>'
                f'<b class="gold">{int(n)}</b></div>'
            )
        return out

    rows = ""
    for country, device, os_name, browser, clicked_at in click_rows:
        rows += f"""
        <tr>
          <td><span class="tag">{html.escape(country or "Unknown")}</span></td>
          <td>{html.escape(device or "Others")}</td>
          <td>{html.escape(os_name or "Others")}</td>
          <td>{html.escape(browser or "Others")}</td>
          <td class="muted">{format_dt(clicked_at)}</td>
        </tr>
        """
    if not rows:
        rows = '<tr><td colspan="5" class="muted" style="text-align:center;padding:48px">No clicks yet.</td></tr>'

    full_short = html.escape(request.host_url.rstrip("/") + "/" + sc)
    body = f"""
    <p><a class="gold" href="/dashboard">← Dashboard</a></p>
    <h1 style="margin-top:12px">/{html.escape(sc)}</h1>
    <p class="muted" style="margin:8px 0 8px;word-break:break-all">{html.escape(original_url)}</p>
    <p style="margin:0 0 12px">
      <a class="gold" href="{full_short}">{full_short}</a>
      <button type="button" class="btn btn-ghost" style="padding:4px 10px;font-size:12px;margin-left:8px"
              onclick="copyText('{full_short}', this)">Copy</button>
    </p>
    <p style="font-family:'Cormorant Garamond',serif;font-size:48px;color:var(--gold)">
      {int(clicks)} <span class="muted" style="font-size:22px">clicks</span>
    </p>
    <p class="muted">Created {format_dt(created_at)}</p>
    <form method="POST" action="/delete/{html.escape(sc)}" style="margin:16px 0"
          onsubmit="return confirm('Delete this link permanently?')">
      <button type="submit" class="btn btn-ghost" style="color:#f87171">Delete this link</button>
    </form>
    <script>
    function copyText(text, btn) {{
      navigator.clipboard.writeText(text).then(function() {{
        var old = btn.textContent;
        btn.textContent = "Copied!";
        setTimeout(function() {{ btn.textContent = old; }}, 1500);
      }}).catch(function() {{
        var ta = document.createElement("textarea");
        ta.value = text; document.body.appendChild(ta); ta.select();
        document.execCommand("copy"); document.body.removeChild(ta);
        var old = btn.textContent;
        btn.textContent = "Copied!";
        setTimeout(function() {{ btn.textContent = old; }}, 1500);
      }});
    }}
    </script>
    <div class="grid3">
      <div class="panel"><p class="kicker">Countries</p>{chips(top_countries)}</div>
      <div class="panel"><p class="kicker">Devices</p>{chips(top_devices)}</div>
      <div class="panel"><p class="kicker">Browsers</p>{chips(top_browsers)}</div>
    </div>
    <div class="card">
      <div class="card-h">
        <h2>Click log</h2>
        <p>Country · device · OS · browser · date · time</p>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Country</th><th>Device</th><th>OS</th><th>Browser</th><th>Date & time</th></tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </div>
    """
    return layout(f"Analytics · /{sc}", body, "dashboard")


@app.route("/api/live-clicks")
@login_required
def live_clicks():
    user = current_user()
    try:
        with get_db() as conn:
            cur = conn.cursor()
            if user["is_admin"]:
                cur.execute("""
                    SELECT id, short_code, country, device, operating_system, browser, clicked_at
                    FROM click_logs ORDER BY id DESC LIMIT 120
                """)
            else:
                cur.execute("""
                    SELECT cl.id, cl.short_code, cl.country, cl.device,
                           cl.operating_system, cl.browser, cl.clicked_at
                    FROM click_logs cl
                    JOIN links l ON l.id = cl.link_id
                    WHERE l.user_id = ?
                    ORDER BY cl.id DESC LIMIT 120
                """, (user["id"],))
            rows = cur.fetchall()
        data = []
        for row in rows:
            data.append({
                "id": row[0],
                "short_code": row[1],
                "country": row[2] or "Unknown",
                "device": row[3] or "Others",
                "os": row[4] or "Others",
                "browser": row[5] or "Others",
                "clicked_at": row[6].isoformat() if row[6] else None,
            })
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/live-console")
@login_required
def live_console():
    body = """
    <p class="kicker">Live</p>
    <h1>Click console</h1>
    <p class="muted" style="margin:8px 0 22px">Only your links (admin sees all).</p>
    <div class="card">
      <input id="search" class="search" placeholder="Search country, device, browser or link…">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Date & time</th><th>Link</th><th>Country</th>
              <th>Device</th><th>OS</th><th>Browser</th>
            </tr>
          </thead>
          <tbody id="events">
            <tr><td colspan="6" class="muted" style="text-align:center;padding:48px">Waiting for clicks…</td></tr>
          </tbody>
        </table>
      </div>
    </div>
    <script>
    let events = [];
    function renderEvents() {
      const q = document.getElementById('search').value.toLowerCase();
      const body = document.getElementById('events');
      const filtered = events.filter(e => (
        e.short_code + ' ' + e.country + ' ' + e.device + ' ' + e.os + ' ' + e.browser
      ).toLowerCase().includes(q));
      if (!filtered.length) {
        body.innerHTML = "<tr><td colspan='6' class='muted' style='text-align:center;padding:48px'>No matching events</td></tr>";
        return;
      }
      body.innerHTML = filtered.map(e => {
        const t = e.clicked_at ? new Date(e.clicked_at).toLocaleString() : '—';
        return `<tr>
          <td class="muted">${t}</td>
          <td><a class="gold" href="/analytics/${e.short_code}">/${e.short_code}</a></td>
          <td><span class="tag">${e.country}</span></td>
          <td>${e.device}</td>
          <td>${e.os}</td>
          <td>${e.browser}</td>
        </tr>`;
      }).join('');
    }
    async function loadEvents() {
      try {
        const res = await fetch('/api/live-clicks', { cache: 'no-store' });
        if (!res.ok) return;
        events = await res.json();
        if (events.error) return;
        renderEvents();
      } catch (err) {}
    }
    document.getElementById('search').addEventListener('input', renderEvents);
    loadEvents();
    setInterval(loadEvents, 2000);
    </script>
    """
    return layout("Live Console · SmartLink", body, "live")


@app.route("/admin")
@admin_required
def admin_panel():
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users")
            total_users = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users WHERE CASE WHEN is_blocked THEN 1 ELSE 0 END = 1")
            blocked_users = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM links")
            total_links = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(SUM(clicks), 0) FROM links")
            total_clicks = cur.fetchone()[0]
            cur.execute("""
                SELECT u.id, u.name, u.email, u.is_admin, CASE WHEN u.is_blocked THEN 1 ELSE 0 END, u.created_at,
                       COUNT(l.id), COALESCE(SUM(l.clicks), 0)
                FROM users u
                LEFT JOIN links l ON l.user_id = u.id
                GROUP BY u.id, u.name, u.email, u.is_admin, u.is_blocked, u.created_at
                ORDER BY u.id DESC
            """)
            users = cur.fetchall()
            cur.execute("""
                SELECT l.short_code, l.original_url, l.clicks, l.created_at, u.email
                FROM links l
                LEFT JOIN users u ON u.id = l.user_id
                ORDER BY l.id DESC LIMIT 100
            """)
            links = cur.fetchall()
    except Exception as e:
        return f"Admin error: {html.escape(str(e))}", 500

    me = current_user()
    user_rows = ""
    for uid, name, email, is_admin, is_blocked, created, link_count, click_count in users:
        if is_admin:
            badge = '<span class="tag">Admin</span>'
        elif is_blocked:
            badge = '<span class="tag" style="color:#f87171">Blocked</span>'
        else:
            badge = '<span class="tag">User</span>'

        actions = ""
        if int(uid) != int(me["id"]):
            if is_blocked:
                actions = (
                    '<form method="POST" action="/admin/unblock/%d" style="display:inline">'
                    '<button class="btn btn-ghost" type="submit" style="padding:6px 12px;font-size:12px">Unblock</button>'
                    '</form>'
                ) % int(uid)
            else:
                actions = (
                    '<form method="POST" action="/admin/block/%d" style="display:inline" '
                    'onsubmit="return confirm(\'Block this user?\')">'
                    '<button class="btn btn-ghost" type="submit" style="padding:6px 12px;font-size:12px;color:#f87171">Block</button>'
                    '</form>'
                ) % int(uid)
            if not is_admin:
                actions += (
                    ' <form method="POST" action="/admin/delete-user/%d" style="display:inline" '
                    'onsubmit="return confirm(\'Delete user and ALL their links forever?\')">'
                    '<button class="btn btn-ghost" type="submit" style="padding:6px 12px;font-size:12px;color:#f87171">Delete</button>'
                    '</form>'
                ) % int(uid)

        user_rows += (
            "<tr>"
            "<td>%d</td>"
            "<td>%s</td>"
            "<td>%s</td>"
            "<td>%s</td>"
            "<td>%d</td>"
            "<td>%d</td>"
            '<td class="muted">%s</td>'
            "<td>%s</td>"
            "</tr>"
        ) % (
            int(uid),
            html.escape(name),
            html.escape(email),
            badge,
            int(link_count),
            int(click_count),
            format_dt(created),
            actions,
        )
    if not user_rows:
        user_rows = '<tr><td colspan="8" class="muted" style="text-align:center;padding:32px">No users</td></tr>'

    link_rows = ""
    for short, url, clicks, created, email in links:
        owner = html.escape(email or "—")
        safe_url = html.escape(url)
        display = safe_url if len(url) < 40 else html.escape(url[:37] + "...")
        sc = html.escape(short)
        link_rows += (
            "<tr>"
            '<td><a class="gold" href="/analytics/%s">/%s</a></td>'
            '<td class="muted">%s</td>'
            "<td>%d</td>"
            "<td>%s</td>"
            '<td class="muted">%s</td>'
            "</tr>"
        ) % (sc, sc, display, int(clicks), owner, format_dt(created))
    if not link_rows:
        link_rows = '<tr><td colspan="5" class="muted" style="text-align:center;padding:32px">No links</td></tr>'

    body = """
    <p class="kicker">Admin</p>
    <h1>Control panel</h1>
    <p class="muted" style="margin-bottom:18px">Only admin can see this. Normal users cannot see other users' details.</p>
    <div class="stats">
      <div class="stat"><b>%d</b><span>Total users</span></div>
      <div class="stat"><b>%d</b><span>Blocked</span></div>
      <div class="stat"><b>%d</b><span>All links</span></div>
      <div class="stat"><b>%d</b><span>All clicks</span></div>
    </div>
    <div class="card" style="margin-bottom:24px">
      <div class="card-h">
        <h2>Users</h2>
        <p>Signups, links, clicks — Block or Delete any user</p>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>ID</th><th>Name</th><th>Email</th><th>Status</th>
              <th>Links</th><th>Clicks</th><th>Joined</th><th>Actions</th>
            </tr>
          </thead>
          <tbody>%s</tbody>
        </table>
      </div>
    </div>
    <div class="card">
      <div class="card-h">
        <h2>Recent links (all users)</h2>
        <p>Latest 100 across the platform</p>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Short</th><th>Destination</th><th>Clicks</th><th>Owner</th><th>Created</th></tr>
          </thead>
          <tbody>%s</tbody>
        </table>
      </div>
    </div>
    """ % (total_users, blocked_users, total_links, total_clicks, user_rows, link_rows)
    return layout("Admin · SmartLink", body, "admin")


@app.route("/admin/block/<int:user_id>", methods=["POST"])
@admin_required
def admin_block_user(user_id):
    me = current_user()
    if user_id == me["id"]:
        return redirect("/admin")
    with get_db() as conn:
        cur = conn.cursor()
        if USE_PG:
            cur.execute(
                "UPDATE users SET is_blocked = TRUE WHERE id = %s AND COALESCE(is_admin, FALSE) = FALSE",
                (user_id,),
            )
        else:
            cur.execute(
                "UPDATE users SET is_blocked = 1 WHERE id = ? AND CASE WHEN is_admin THEN 1 ELSE 0 END = 0",
                (user_id,),
            )
        conn.commit()
    return redirect("/admin")


@app.route("/admin/unblock/<int:user_id>", methods=["POST"])
@admin_required
def admin_unblock_user(user_id):
    with get_db() as conn:
        cur = conn.cursor()
        if USE_PG:
            cur.execute("UPDATE users SET is_blocked = FALSE WHERE id = %s", (user_id,))
        else:
            cur.execute("UPDATE users SET is_blocked = 0 WHERE id = ?", (user_id,))
        conn.commit()
    return redirect("/admin")


@app.route("/admin/delete-user/<int:user_id>", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    me = current_user()
    if user_id == me["id"]:
        return redirect("/admin")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        if row and row[0]:
            return redirect("/admin")
        cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    return redirect("/admin")


@app.route("/delete/<short_code>", methods=["POST"])
@login_required
def delete_link(short_code):
    user = current_user()
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, user_id FROM links WHERE short_code = ?",
                (short_code,),
            )
            row = cur.fetchone()
            if not row:
                abort(404)
            link_id, owner_id = row
            # Owner or admin only
            if int(owner_id) != int(user["id"]) and not user["is_admin"]:
                abort(403)
            cur.execute("DELETE FROM click_logs WHERE link_id = ?", (link_id,))
            cur.execute("DELETE FROM links WHERE id = ?", (link_id,))
            conn.commit()
    except Exception as e:
        code = getattr(e, "code", None)
        if code in (403, 404):
            raise
        return f"Delete failed: {html.escape(str(e))}", 500
    return redirect("/dashboard")


@app.route("/support")
def support():
    return redirect(SUPPORT_URL)


@app.route("/<short_code>", methods=["GET", "HEAD"])
def redirect_short_link(short_code):
    reserved = {
        "create", "dashboard", "analytics", "live-console", "health", "api",
        "support", "signup", "signin", "signout", "admin", "delete",
    }
    if short_code in reserved:
        abort(404)

    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, original_url FROM links WHERE short_code = ?",
                (short_code,),
            )
            link = cur.fetchone()
    except Exception:
        return "Service temporarily unavailable", 503

    if not link:
        abort(404)

    link_id, original_url = link

    # Only count real GET clicks — skip HEAD / bots / rapid duplicates
    try:
        if request.method != "GET":
            return redirect(original_url, code=302)

        user_agent = request.headers.get("User-Agent", "")
        if is_bot(user_agent):
            return redirect(original_url, code=302)

        # Prefetch / background requests
        purpose = (request.headers.get("Purpose") or request.headers.get("Sec-Purpose") or "").lower()
        if "prefetch" in purpose or "preview" in purpose:
            return redirect(original_url, code=302)

        ip = get_client_ip()
        ip_hash_value = hash_ip(ip)

        with get_db() as conn:
            cur = conn.cursor()
            # Dedup: same visitor + same link within 15 seconds = 1 click
            if USE_PG:
                cur.execute(
                    """
                    SELECT 1 FROM click_logs
                    WHERE short_code = ? AND ip_hash = ?
                      AND clicked_at > (NOW() - INTERVAL '15 seconds')
                    LIMIT 1
                    """,
                    (short_code, ip_hash_value),
                )
            else:
                cur.execute(
                    """
                    SELECT 1 FROM click_logs
                    WHERE short_code = ? AND ip_hash = ?
                      AND clicked_at > datetime('now', '-15 seconds')
                    LIMIT 1
                    """,
                    (short_code, ip_hash_value),
                )
            if cur.fetchone():
                return redirect(original_url, code=302)

            country = get_country(ip, user_agent)
            device = get_device(user_agent)
            operating_system = get_operating_system(user_agent)
            browser = get_browser(user_agent)
            cur.execute("UPDATE links SET clicks = clicks + 1 WHERE id = ?", (link_id,))
            cur.execute(
                """
                INSERT INTO click_logs
                (link_id, short_code, country, device, operating_system, browser, ip_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (link_id, short_code, country, device, operating_system, browser, ip_hash_value),
            )
            conn.commit()
    except Exception:
        pass

    return redirect(original_url, code=302)


@app.route("/health")
def health():
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            ok = cur.fetchone()[0] == 1
            users = 0
            links = 0
            try:
                cur.execute("SELECT COUNT(*) FROM users")
                users = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM links")
                links = cur.fetchone()[0]
            except Exception:
                pass
        env_keys = [k for k in os.environ.keys() if "DATABASE" in k.upper() or "POSTGRES" in k.upper()]
        return jsonify({
            "status": "ok",
            "database": ok,
            "db_type": "postgres" if USE_PG else "sqlite",
            "users": users,
            "links": links,
            "has_database_url": bool(DATABASE_URL),
            "env_keys_found": env_keys,
            "hint": "Add DATABASE_URL on Web Service Environment, then Manual Deploy" if not USE_PG else "postgres connected",
        })
    except Exception as error:
        env_keys = [k for k in os.environ.keys() if "DATABASE" in k.upper() or "POSTGRES" in k.upper()]
        return jsonify({
            "status": "error",
            "database": False,
            "db_type": "postgres" if USE_PG else "sqlite",
            "has_database_url": bool(DATABASE_URL),
            "env_keys_found": env_keys,
            "error": str(error),
            "hint": "Add DATABASE_URL on Web Service Environment, then Manual Deploy",
        }), 500


# Init DB on startup
try:
    setup_database()
    print("Database ready | mode:", "POSTGRES (persistent)" if USE_PG else "SQLITE (will reset on redeploy)")
except Exception as exc:
    print("database setup error:", exc)

if __name__ == "__main__":
    print("SMARTLINK · Shuvo Ahmed")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), debug=False)
