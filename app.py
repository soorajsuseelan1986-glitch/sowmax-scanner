"""Sowmax Scanner — Flask server. Run:  .venv/bin/python app.py  then open http://127.0.0.1:5000"""
import hmac
import os
import secrets
import threading
import time
import warnings
from datetime import timedelta
from html import escape
from pathlib import Path
from urllib.parse import quote

from flask import Flask, abort, jsonify, redirect, request, send_from_directory, session

warnings.filterwarnings("ignore")

import feeds  # noqa: E402
import groww_data  # noqa: E402
import kite_data  # noqa: E402
import magic  # noqa: E402
import scanner  # noqa: E402
from scanner import chart_data, run_scan  # noqa: E402
from universe import UNIVERSES  # noqa: E402

app = Flask(__name__, static_folder="static", static_url_path="")
app.json.sort_keys = False

# ---- Login -------------------------------------------------------------------
# Credentials come from environment variables (set them in Render → Environment,
# or in a local .env file that run.sh loads). If they're not set, nobody can log in.
APP_USERNAME = os.environ.get("APP_USERNAME", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),  # HTTPS-only cookie on Render
)
PUBLIC_PATHS = {"/login", "/style.css"}
LOGIN_HTML = (Path(__file__).parent / "static" / "login.html").read_text()


def _safe_next(target):
    return target if target and target.startswith("/") and not target.startswith("//") else "/"


@app.before_request
def require_login():
    if request.path in PUBLIC_PATHS or session.get("user"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "login required"}), 401
    return redirect("/login?next=" + quote(request.full_path.rstrip("?")))


def _login_page(error="", status=200):
    if not (APP_USERNAME and APP_PASSWORD):
        error = "Login isn't set up yet. Set APP_USERNAME and APP_PASSWORD on the server."
    msg = f'<p class="err">{escape(error)}</p>' if error else ""
    nxt = escape(_safe_next(request.values.get("next")))
    return LOGIN_HTML.replace("{{error}}", msg).replace("{{next}}", nxt), status


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return redirect("/") if session.get("user") else _login_page()
    user = request.form.get("username", "")
    pw = request.form.get("password", "")
    ok = (APP_USERNAME and APP_PASSWORD
          and hmac.compare_digest(user.encode(), APP_USERNAME.encode())
          and hmac.compare_digest(pw.encode(), APP_PASSWORD.encode()))
    if not ok:
        time.sleep(1)  # slow down password guessing
        return _login_page("Wrong user ID or password.", 401)
    session.clear()
    session.permanent = True
    session["user"] = user
    return redirect(_safe_next(request.form.get("next")))


@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect("/login")


# ---- Zerodha Kite ------------------------------------------------------------
# The access token lives in server memory and is also kept in your (signed, HTTPS-only) session
# cookie, so it survives Render's free plan going to sleep. Kite expires it daily at 06:00 IST.

@app.before_request
def restore_kite():
    k = session.get("kite")
    if k and not kite_data.connected():
        if not kite_data.restore(k.get("token"), k.get("user"), k.get("since")):
            if not kite_data.token_fresh(k.get("since")):
                session.pop("kite", None)


def _clear_caches():
    scanner._cache.clear()
    scanner._idx_cache["ts"] = 0
    magic._cache.clear()


@app.get("/kite/login")
def kite_login():
    if not kite_data.configured():
        return "Zerodha isn't set up: add KITE_API_KEY and KITE_API_SECRET on the server.", 503
    return redirect(kite_data.login_url())


@app.get("/kite/callback")
def kite_callback():
    token = request.args.get("request_token")
    if request.args.get("status") != "success" or not token:
        return redirect("/?kite=failed")
    try:
        access, user, since = kite_data.connect(token)
    except Exception as e:
        print("kite connect failed:", e)
        return redirect("/?kite=failed")
    session["kite"] = {"token": access, "user": user, "since": since}
    _clear_caches()
    return redirect("/?kite=connected")


@app.get("/kite/disconnect")
def kite_disconnect():
    session.pop("kite", None)
    kite_data.disconnect()
    _clear_caches()
    return redirect("/")


@app.get("/api/feed")
def feed_status():
    g, k = groww_data.status(), kite_data.status()  # check logins first, then report the active feed
    return jsonify({"source": feeds.name(), "groww": g, "kite": k})


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/universes")
def universes():
    return jsonify({k: v["name"] for k, v in UNIVERSES.items()})


@app.get("/api/scan/<mode>")
def scan(mode):
    if mode not in ("intraday", "swing"):
        abort(404)
    universe = request.args.get("universe", "nifty50")
    if universe not in UNIVERSES:
        abort(400, "unknown universe")
    return jsonify(run_scan(mode, universe, force=request.args.get("force") == "1"))


@app.get("/api/chart/<path:symbol>")
def chart(symbol):
    tf = request.args.get("tf") or ("5m" if request.args.get("mode") == "intraday" else "1d")
    data = chart_data(symbol.upper(), tf)
    if not data:
        abort(404)
    return jsonify(data)


@app.get("/api/magic")
def magic_summary():
    from scanner import get_indices, market_status
    status, label = market_status()
    return jsonify({"indices": magic.summary(), "bar": get_indices(), "market": {"status": status, "label": label}})


@app.get("/api/magic/<key>")
def magic_detail(key):
    key = key.upper()
    if key not in magic.INDICES:
        abort(404)
    data = magic.detail(key)
    if not data:
        abort(503)
    return jsonify(data)



def _warm_up():
    """Fill the caches right after the server starts, so the first page load doesn't wait for it."""
    for fn, args in ((run_scan, ("intraday", "nifty50")), (magic.summary, ()), (run_scan, ("swing", "nifty50"))):
        try:
            fn(*args)
        except Exception as e:
            print("warm-up step failed:", e)


if os.environ.get("WARM_UP", "1") == "1":
    threading.Thread(target=_warm_up, daemon=True).start()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
