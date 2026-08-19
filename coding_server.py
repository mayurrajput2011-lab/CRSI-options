"""
coding_server.py  —  Local, offline coding assistant for the NIFTY project.
=============================================================================

A tiny Flask app that serves `coding.html` (a claude.ai-style chat UI) and
proxies everything to a LOCALLY installed Ollama model. Nothing ever leaves
your machine — no API keys, no tokens, no cloud.

WHY NOT "Claude on Ollama"?
    Anthropic does not distribute Claude's weights, so there is no
    `ollama pull claude`. Instead we use the strongest OPEN coding models,
    which run fully local. Recommended (pick by your RAM/VRAM):
        ollama pull qwen2.5-coder:7b      # great default, ~5 GB
        ollama pull qwen2.5-coder:14b     # better, ~9 GB
        ollama pull deepseek-coder-v2     # strong alternative
        ollama pull codestral             # Mistral's coder, ~13 GB

QUICK START
    1) Install Ollama            -> https://ollama.com/download
    2) ollama pull qwen2.5-coder:7b
    3) pip install flask flask-cors requests
    4) Put this file + coding.html in the SAME folder as your project
       (server.py, screener.py, index.html) so the model can read them.
    5) python coding_server.py
    6) Open http://localhost:5070

It runs on port 5070 so it never clashes with your journal on 5050.
"""

import os
import json
import requests
from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
PORT       = int(os.environ.get("CODING_PORT", "5070"))
HERE       = os.path.dirname(os.path.abspath(__file__))

# Only these extensions are exposed as "project context" in the UI. This is a
# safety boundary: the model can only ever see source files in THIS folder,
# never your whole disk.
ALLOWED_EXT = (".py", ".html", ".js", ".css", ".json", ".txt", ".md", ".sql")
MAX_FILE_BYTES = 400_000  # skip anything huge so we don't blow the context window

SYSTEM_PROMPT = (
    "You are a precise, senior software engineer acting as a local coding "
    "assistant for a Python Flask project (a NIFTY options trade journal + "
    "stock screener). The stack is: Flask + SQLite backend (server.py), a "
    "yfinance-based screener/backtester (screener.py), and a single-file "
    "vanilla-JS frontend (index.html) with a dark amber theme. "
    "When the user attaches files, treat them as the CURRENT source of truth. "
    "Give complete, correct, runnable code. Prefer minimal diffs over rewrites, "
    "and clearly state which file each change goes in and where. Point out bugs "
    "and edge cases you notice. Keep explanations tight."
)

app = Flask(__name__, static_folder=None)
CORS(app)


# ---------------------------------------------------------------------------
# Static: serve the chat page
# ---------------------------------------------------------------------------
@app.route("/")
def root():
    return send_from_directory(HERE, "coding.html")


@app.route("/coding.html")
def coding_page():
    return send_from_directory(HERE, "coding.html")


# ---------------------------------------------------------------------------
# Health: is Ollama reachable?
# ---------------------------------------------------------------------------
@app.route("/api/coding/health")
def health():
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        return jsonify({"ok": r.status_code == 200})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Models: list what the user has pulled locally
# ---------------------------------------------------------------------------
@app.route("/api/coding/models")
def models():
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        names = [m["name"] for m in r.json().get("models", [])]
        return jsonify({"models": sorted(names)})
    except Exception as e:
        return jsonify({"models": [], "error": str(e)}), 502


# ---------------------------------------------------------------------------
# Project files: list + read (sandboxed to THIS folder only)
# ---------------------------------------------------------------------------
def _safe_path(name):
    """Resolve `name` to a path inside HERE only — blocks ../ traversal."""
    p = os.path.abspath(os.path.join(HERE, name))
    if not p.startswith(HERE + os.sep):
        return None
    return p


@app.route("/api/coding/files")
def list_files():
    out = []
    for name in sorted(os.listdir(HERE)):
        full = os.path.join(HERE, name)
        if (os.path.isfile(full)
                and name.lower().endswith(ALLOWED_EXT)
                and name != "coding.html"):
            out.append({"name": name, "size": os.path.getsize(full)})
    return jsonify({"files": out})


@app.route("/api/coding/file")
def read_file():
    name = request.args.get("name", "")
    if not name.lower().endswith(ALLOWED_EXT):
        return jsonify({"error": "file type not allowed"}), 400
    path = _safe_path(name)
    if not path or not os.path.isfile(path):
        return jsonify({"error": "not found"}), 404
    if os.path.getsize(path) > MAX_FILE_BYTES:
        return jsonify({"error": "file too large to attach"}), 413
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return jsonify({"name": name, "content": f.read()})


# ---------------------------------------------------------------------------
# Chat: stream tokens from Ollama straight to the browser
# ---------------------------------------------------------------------------
@app.route("/api/coding/chat", methods=["POST"])
def chat():
    data = request.json or {}
    model = data.get("model")
    user_messages = data.get("messages", [])
    temperature = data.get("temperature", 0.2)

    if not model:
        return jsonify({"error": "no model selected"}), 400

    # Prepend our system prompt so every conversation is project-aware.
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + user_messages,
        "stream": True,
        "options": {"temperature": float(temperature)},
    }

    def generate():
        try:
            with requests.post(
                f"{OLLAMA_URL}/api/chat",
                json=payload,
                stream=True,
                timeout=(10, None),  # no read timeout: long generations are fine
            ) as resp:
                if resp.status_code != 200:
                    yield json.dumps({"error": f"Ollama HTTP {resp.status_code}: "
                                               f"{resp.text[:200]}"}) + "\n"
                    return
                # Ollama streams newline-delimited JSON; relay each line as-is.
                for line in resp.iter_lines(decode_unicode=True):
                    if line:
                        yield line + "\n"
        except requests.exceptions.ConnectionError:
            yield json.dumps({"error": "Cannot reach Ollama. Run `ollama serve` "
                                       "and make sure the model is pulled."}) + "\n"
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"

    return Response(generate(), mimetype="application/x-ndjson")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Local Coder  ->  http://localhost:%d" % PORT)
    print("  Ollama endpoint   :  %s" % OLLAMA_URL)
    print("  Serving files from:  %s" % HERE)
    print("=" * 60)

    # Fail loudly if the page is missing, rather than 404-ing later.
    if not os.path.isfile(os.path.join(HERE, "coding.html")):
        print("\n  ERROR: coding.html is not in this folder.")
        print("  Put coding.html next to coding_server.py, then re-run.\n")
        input("  Press Enter to close...")
        raise SystemExit(1)

    try:
        app.run(debug=False, port=PORT, threaded=True)
    except OSError as e:
        # Most common: port already in use, or blocked.
        print("\n  ERROR: could not start the server on port %d." % PORT)
        print("  Details: %s" % e)
        print("  Is another copy already running? Close it and try again,")
        print("  or change the port:  set CODING_PORT=5080  then re-run.\n")
        input("  Press Enter to close...")
        raise SystemExit(1)
    except Exception as e:
        print("\n  ERROR while starting: %s\n" % e)
        input("  Press Enter to close...")
        raise SystemExit(1)
