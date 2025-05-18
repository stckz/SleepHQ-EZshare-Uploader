from flask import Flask, render_template_string, request, send_file, jsonify, abort
import subprocess
import os
import json
import sys

app = Flask(__name__)

LOG_PATH = "uploader.log"
ZIP_PATH = "cpapdata.zip"
HISTORY_PATH = "upload_history.json"
ERROR_LOG_PATH = "upload_errors.log"
DOWNLOAD_ROOT = "downloads"

STYLE = """
    <style>
        body { font-family: Inter, sans-serif; background: #111827; color: #d1d5db; padding: 2rem; }
        h1, h2, h3, label { color: #f9fafb; }
        .container { background: #1f2937; border-radius: 0.5rem; padding: 2rem; margin-bottom: 2rem; box-shadow: 0 0 10px rgba(0,0,0,0.5); }
        .explorer { background: #374151; border-radius: 0.5rem; margin-top: 1rem; padding: 1rem; box-shadow: inset 0 0 2px rgba(0,0,0,0.3); }
        pre { background: #374151; padding: 1rem; border-radius: 0.25rem; overflow-x: auto; color: #d1d5db; max-height: 300px; }
        input[type=text] { padding: 0.5rem; font-size: 1rem; border-radius: 0.25rem; background: #374151; border: 1px solid #4b5563; color: #f3f4f6; width: 200px; }
        input[type=submit], a.button {
            background: #2563eb; color: white; padding: 0.5rem 1rem;
            border: none; border-radius: 0.375rem; cursor: pointer;
            text-decoration: none; display: inline-block; margin-top: 0.5rem;
        }
        input[type=submit]:hover, a.button:hover { background: #1d4ed8; }
        a { color: #3b82f6; text-decoration: none; }
        a:hover { text-decoration: underline; }
        details summary { cursor: pointer; font-weight: bold; }
        .error-block pre { background: #7f1d1d; color: #fee2e2; }
        .nav-buttons a { margin-right: 1rem; }
        ul.file-tree { list-style-type: none; padding-left: 1rem; }
        .file-entry { margin: 0.25rem 0; }
        .hidden { display: none; }
        .arrow { display: inline-block; width: 1em; color: #fbbf24; }
    </style>
    <script>
        async function toggleFolder(el, path) {
            const arrow = el.querySelector(".arrow");
            const isLoaded = el.getAttribute("data-loaded");

            if (isLoaded === "true") {
                const ul = el.querySelector("ul");
                ul.classList.toggle("hidden");
                arrow.textContent = ul.classList.contains("hidden") ? "▶" : "▼";
                return;
            }

            const res = await fetch("/api/list-dir?path=" + encodeURIComponent(path));
            const data = await res.json();
            const ul = document.createElement("ul");
            ul.classList.add("file-tree");

            data.forEach(entry => {
                const li = document.createElement("li");
                li.classList.add("file-entry");

                if (entry.is_dir) {
                    const wrapper = document.createElement("span");
                    wrapper.innerHTML = '<span class="arrow">▶</span> ' + entry.name;
                    wrapper.style.cursor = "pointer";
                    wrapper.onclick = () => toggleFolder(li, entry.path);
                    li.appendChild(wrapper);
                } else {
                    const link = document.createElement("a");
                    link.innerText = "📄 " + entry.name;
                    link.href = "/download?path=" + encodeURIComponent(entry.path);
                    li.appendChild(link);
                }

                ul.appendChild(li);
            });

            el.appendChild(ul);
            el.setAttribute("data-loaded", "true");
            arrow.textContent = "▼";
        }
    </script>
"""

@app.route("/", methods=["GET", "POST"])
def dashboard():
    message = ""
    if request.method == "POST":
        date = request.form["date"]
        result = subprocess.run(
            [sys.executable, "sleep.py", "--force-date", date],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            message = f"<span style='color:#10b981;'>Upload from {date}: Success</span>"
        else:
            message = f"""<span style='color:#f87171;'>Upload from {date}: Error</span>
            <div class="error-block"><b>stderr:</b><pre>{result.stderr}</pre>
            <b>stdout:</b><pre>{result.stdout}</pre></div>"""

    try:
        with open(LOG_PATH) as f:
            log_output = "".join(f.readlines()[-100:])
    except FileNotFoundError:
        log_output = "Log file not found."

    html = f"""
    <!doctype html>
    <html><head><title>SleepHQ Uploader</title>{STYLE}</head>
    <body>
    <div class="container">
        <h1>SleepHQ Uploader</h1>
        <form method="post">
            <label for="date">Force Upload from Date (YYYYMMDD)</label><br>
            <input type="text" id="date" name="date" pattern="\\d{{8}}" placeholder="e.g. 20250513" required>
            <br><input type="submit" value="Upload">
        </form>
        <div style="margin-top: 1rem;">{message}</div>
    </div>

    <div class="container">
        <details>
            <summary>📜 Show Last 100 Log Lines</summary>
            <pre>{log_output}</pre>
        </details>
    </div>

    <div class="container">
        <a class="button" href="/history">📈 View Upload History</a>
        <a class="button" href="/files">📁 Browse Files</a>
        <a class="button" href="/download">⬇️ Download ZIP</a>
        <a class="button" href="/errors">⚠️ Error Log</a>
    </div>
    </body></html>
    """
    return render_template_string(html)

@app.route("/files")
def files():
    html = f"""
    <!doctype html>
    <html><head><title>File Explorer</title>{STYLE}</head>
    <body>
    <div class="container container">
        <h1>File Explorer</h1>
        <a href="/" class="button">← Home</a>
        <br />
        <div class="container explorer">
        <ul class="file-tree">
            <li class="file-entry" data-loaded="false">
                <span class="arrow">▶</span>
                <span style="cursor:pointer" onclick="toggleFolder(this.parentElement, '{DOWNLOAD_ROOT}')">downloads</span>
            </li>
        </ul>
    </div></div></body></html>
    """
    return render_template_string(html)

@app.route("/api/list-dir")
def api_list_dir():
    path = os.path.abspath(request.args.get("path", DOWNLOAD_ROOT))
    root = os.path.abspath(DOWNLOAD_ROOT)
    if not path.startswith(root) or not os.path.isdir(path):
        abort(400)
    entries = []
    for entry in sorted(os.listdir(path)):
        full = os.path.join(path, entry)
        entries.append({
            "name": entry,
            "path": os.path.abspath(full),
            "is_dir": os.path.isdir(full)
        })
    return jsonify(entries)

@app.route("/download")
def download_zip():
    path = request.args.get("path")
    if not path:
        return "Missing path", 400
    full = os.path.abspath(path)
    root = os.path.abspath(DOWNLOAD_ROOT)
    if not full.startswith(root) or not os.path.isfile(full):
        return "Invalid file path", 403
    return send_file(full, as_attachment=True)

@app.route("/history")
def history():
    entries = []
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH) as f:
            for line in f:
                try:
                    entries.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    continue
    entries = list(reversed(entries[-50:]))

    html = f"""
    <!doctype html>
    <html><head><title>Upload History</title>{STYLE}</head>
    <body>
    <div class="container">
        <h1>Upload History</h1>
        <a href="/" class="button">← Home</a>
        <table>
            <tr><th>Timestamp</th><th>Date</th><th>Status</th><th>Hash</th><th>Duration (s)</th></tr>
            {''.join(f"<tr><td>{e['timestamp']}</td> <td>{e['date']}</td> <td>{e['status']}</td> <td>{e['hash']}</td> <td>{e['duration_sec']}</td> </tr>" for e in entries)}
        </table>
    </div></body></html>
    """
    return render_template_string(html)

@app.route("/errors")
def errors():
    try:
        with open(ERROR_LOG_PATH) as f:
            lines = f.readlines()[-20:]
    except FileNotFoundError:
        lines = ["No errors logged."]
    html = f"""
    <!doctype html>
    <html><head><title>Error Log</title>{STYLE}</head>
    <body>
    <div class="container">
        <h1>Error Log</h1>
        <a href="/" class="button">← Home</a>
        <pre>{''.join(lines)}</pre>
    </div></body></html>
    """
    return render_template_string(html)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)