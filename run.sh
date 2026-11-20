#!/bin/zsh
# Start the Sowmax Scanner at http://127.0.0.1:5000
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
fi
(sleep 2 && open http://127.0.0.1:5000) &
.venv/bin/python -W ignore app.py
