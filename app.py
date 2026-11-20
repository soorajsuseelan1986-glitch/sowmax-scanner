"""Sowmax Scanner — Flask server. Run:  .venv/bin/python app.py  then open http://127.0.0.1:5000"""
import warnings

from flask import Flask, abort, jsonify, request, send_from_directory

warnings.filterwarnings("ignore")

from scanner import chart_data, run_scan  # noqa: E402
from universe import UNIVERSES  # noqa: E402
import magic  # noqa: E402

app = Flask(__name__, static_folder="static", static_url_path="")
app.json.sort_keys = False


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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
