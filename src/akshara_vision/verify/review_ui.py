"""Local Human Review Queue UI server for Akshara Verify (lightweight HTTP server)."""
import http.server
import json
import socketserver
import threading
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

from akshara_vision.verify.classifier import calibrate_profile_from_reviews
from akshara_vision.verify.models import AcceptanceProfile, load_acceptance_profile

# Global lock to serialise concurrent writes to review_decisions.json
_decisions_lock = threading.Lock()

# Maximum accepted Content-Length for POST bodies (1 MB)
_MAX_BODY_BYTES = 1_048_576


class ReviewRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler for local QA human review UI."""

    batch_dir: Path
    report_dir: Path
    decisions_file: Path

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_generate_review_html().encode("utf-8"))
            return

        if path == "/api/data":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                json.dumps(self._get_review_payload()).encode("utf-8")
            )
            return

        if path.startswith("/image/"):
            rel_path = urllib.parse.unquote(path[len("/image/") :])
            # Resolve and guard against path traversal
            try:
                img_path = (self.batch_dir / rel_path).resolve()
            except Exception:
                self.send_error(400, "Invalid path")
                return
            batch_resolved = self.batch_dir.resolve()
            try:
                img_path.relative_to(batch_resolved)
            except ValueError:
                self.send_error(403, "Access denied")
                return
            if not img_path.exists() or not img_path.is_file():
                self.send_error(404, "Image not found")
                return
            self.send_response(200)
            suffix = img_path.suffix.lower()
            content_type = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".tif": "image/tiff",
                ".tiff": "image/tiff",
            }.get(suffix, "application/octet-stream")
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(img_path.read_bytes())
            return

        self.send_error(404, "Not found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/decision":
            content_len = min(int(self.headers.get("Content-Length", 0) or 0), _MAX_BODY_BYTES)
            body_bytes = self.rfile.read(content_len)
            try:
                payload = json.loads(body_bytes.decode("utf-8"))
                self._save_decision(payload)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))
            except Exception as exc:
                self.send_error(400, f"Invalid JSON payload: {exc}")
            return

        if path == "/api/calibrate":
            try:
                base_profile = AcceptanceProfile()
                calibrated = calibrate_profile_from_reviews(
                    self.decisions_file, base_profile
                )
                output_profile_path = self.report_dir / "calibrated-profile.toml"
                from akshara_vision.core.toml_compat import dump_toml

                output_profile_path.write_text(
                    dump_toml(calibrated.to_dict()), encoding="utf-8"
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "status": "calibrated",
                            "profile_path": str(output_profile_path),
                        }
                    ).encode("utf-8")
                )
            except Exception as exc:
                self.send_error(500, f"Calibration failed: {exc}")
            return

        self.send_error(404, "POST endpoint not found")

    def _get_review_payload(self) -> Dict[str, Any]:
        report_json = self.report_dir / "machine-report.json"
        exceptions = []
        if report_json.exists():
            try:
                data = json.loads(report_json.read_text(encoding="utf-8"))
                exceptions = data.get("exceptions", [])
            except Exception:
                exceptions = []

        decisions = {}
        if self.decisions_file.exists():
            try:
                dec_data = json.loads(
                    self.decisions_file.read_text(encoding="utf-8")
                )
                decisions = dec_data.get("decisions", {})
            except Exception:
                decisions = {}

        return {
            "batch_dir": str(self.batch_dir),
            "exceptions": exceptions,
            "decisions": decisions,
        }

    def _save_decision(self, payload: Dict[str, Any]) -> None:
        file_path = payload.get("file_path", "")
        if not file_path:
            return
        with _decisions_lock:
            decisions_data: Dict[str, Any] = {"decisions": {}}
            if self.decisions_file.exists():
                try:
                    decisions_data = json.loads(
                        self.decisions_file.read_text(encoding="utf-8")
                    )
                except Exception:
                    decisions_data = {"decisions": {}}

            decisions_data.setdefault("decisions", {})[file_path] = {
                "action": payload.get("action"),
                "check_name": payload.get("check_name"),
                "notes": payload.get("notes", ""),
            }

            self.decisions_file.write_text(
                json.dumps(decisions_data, indent=2), encoding="utf-8"
            )


def _generate_review_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Akshara Verify — Local Human Review Queue</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: #16161a;
            color: #ece7e2;
            margin: 0;
            padding: 0;
            display: flex;
            height: 100vh;
            overflow: hidden;
        }
        .sidebar {
            width: 380px;
            background: #242629;
            border-right: 1px solid #2d2d3a;
            display: flex;
            flex-direction: column;
            height: 100%;
        }
        .sidebar-header {
            padding: 20px;
            border-bottom: 1px solid #2d2d3a;
        }
        .sidebar-header h2 {
            margin: 0 0 8px 0;
            font-size: 20px;
            color: #fffffe;
        }
        .queue-list {
            flex: 1;
            overflow-y: auto;
        }
        .queue-item {
            padding: 14px 20px;
            border-bottom: 1px solid #2d2d3a;
            cursor: pointer;
            transition: background 0.2s;
        }
        .queue-item:hover, .queue-item.active {
            background: #2d2d3a;
        }
        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: bold;
            margin-bottom: 6px;
        }
        .badge.CRITICAL { background: #ef444422; color: #ef4444; }
        .badge.HIGH { background: #f9731622; color: #f97316; }
        .badge.MEDIUM { background: #eab30822; color: #eab308; }
        .badge.LOW { background: #3b82f622; color: #3b82f6; }

        .item-file {
            font-weight: 600;
            margin-bottom: 4px;
            word-break: break-all;
        }
        .item-message {
            font-size: 13px;
            color: #94a1b2;
        }

        .main-panel {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: #16161a;
        }
        .toolbar {
            padding: 16px 24px;
            background: #242629;
            border-bottom: 1px solid #2d2d3a;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .viewer {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            overflow: auto;
        }
        .viewer img {
            max-width: 100%;
            max-height: 100%;
            border: 1px solid #2d2d3a;
            border-radius: 4px;
            background: #242629;
        }
        .actions {
            padding: 16px 24px;
            background: #242629;
            border-top: 1px solid #2d2d3a;
            display: flex;
            gap: 12px;
        }
        button {
            padding: 10px 20px;
            border-radius: 6px;
            border: none;
            font-weight: 600;
            cursor: pointer;
            transition: opacity 0.2s;
        }
        button:hover { opacity: 0.9; }
        .btn-approve { background: #22c55e; color: #fff; }
        .btn-rescan { background: #ef4444; color: #fff; }
        .btn-metadata { background: #eab308; color: #16161a; }
        .btn-calibrate { background: #3b82f6; color: #fff; margin-left: auto; }
    </style>
</head>
<body>
    <div class="sidebar">
        <div class="sidebar-header">
            <h2>Akshara Verify QA Queue</h2>
            <div id="stats" style="font-size: 13px; color: #94a1b2;">Loading exceptions...</div>
        </div>
        <div class="queue-list" id="queue"></div>
    </div>
    <div class="main-panel">
        <div class="toolbar">
            <div id="current-title" style="font-size: 16px; font-weight: 600;">Select an exception to review</div>
            <button class="btn-calibrate" onclick="calibrateProfile()">Tune Profile Thresholds</button>
        </div>
        <div class="viewer" id="viewer">
            <div style="color: #94a1b2;">No document selected</div>
        </div>
        <div class="actions">
            <button class="btn-approve" onclick="submitDecision('approve')">✓ Approve (Override)</button>
            <button class="btn-rescan" onclick="submitDecision('rescan')">⟳ Mark for Rescan</button>
            <button class="btn-metadata" onclick="submitDecision('fix_metadata')">✎ Fix Metadata</button>
        </div>
    </div>

    <script>
        let exceptions = [];
        let decisions = {};
        let currentIndex = -1;

        async function loadData() {
            const res = await fetch('/api/data');
            const data = await res.json();
            exceptions = data.exceptions || [];
            decisions = data.decisions || {};
            renderQueue();
            if (exceptions.length > 0) {
                selectItem(0);
            }
        }

        function renderQueue() {
            const queueEl = document.getElementById('queue');
            queueEl.innerHTML = '';
            document.getElementById('stats').textContent = exceptions.length + ' Flagged Exceptions';

            exceptions.forEach((exc, idx) => {
                const el = document.createElement('div');
                el.className = 'queue-item' + (idx === currentIndex ? ' active' : '');
                const filename = exc.file_path ? exc.file_path.split('/').pop() : 'Unknown';
                const dec = decisions[exc.file_path];
                const statusIcon = dec ? (dec.action === 'approve' ? ' ✓' : ' ⟳') : '';

                const badge = document.createElement('div');
                badge.className = 'badge ' + (exc.severity || '');
                badge.textContent = exc.severity || '';

                const fileDiv = document.createElement('div');
                fileDiv.className = 'item-file';
                fileDiv.textContent = filename + statusIcon;

                const msgDiv = document.createElement('div');
                msgDiv.className = 'item-message';
                msgDiv.textContent = (exc.check_name || '') + ': ' + (exc.message || '');

                el.appendChild(badge);
                el.appendChild(fileDiv);
                el.appendChild(msgDiv);
                el.onclick = () => selectItem(idx);
                queueEl.appendChild(el);
            });
        }

        function selectItem(idx) {
            currentIndex = idx;
            renderQueue();
            const exc = exceptions[idx];
            if (!exc) return;

            const filename = exc.file_path ? exc.file_path.split('/').pop() : 'Unknown';
            const titleEl = document.getElementById('current-title');
            titleEl.textContent = filename + ' — ' + exc.message;

            const viewerEl = document.getElementById('viewer');
            const lc = filename.toLowerCase();
            if (lc.endsWith('.tif') || lc.endsWith('.tiff') || lc.endsWith('.jpg') || lc.endsWith('.jpeg') || lc.endsWith('.png')) {
                const img = document.createElement('img');
                img.src = '/image/' + encodeURIComponent(filename);
                img.alt = filename;
                viewerEl.innerHTML = '';
                viewerEl.appendChild(img);
            } else {
                const msg = document.createElement('div');
                msg.style.color = '#94a1b2';
                msg.textContent = 'Preview not available for ' + filename;
                viewerEl.innerHTML = '';
                viewerEl.appendChild(msg);
            }
        }

        async function submitDecision(action) {
            const exc = exceptions[currentIndex];
            if (!exc) return;

            await fetch('/api/decision', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_path: exc.file_path,
                    check_name: exc.check_name,
                    action: action
                })
            });

            decisions[exc.file_path] = { action: action, check_name: exc.check_name };
            renderQueue();
            if (currentIndex + 1 < exceptions.length) {
                selectItem(currentIndex + 1);
            }
        }

        async function calibrateProfile() {
            const res = await fetch('/api/calibrate', { method: 'POST' });
            const data = await res.json();
            if (data.status === 'calibrated') {
                alert(`Profile calibrated from QA reviews! Saved to: ${data.profile_path}`);
            } else {
                alert('Calibration error.');
            }
        }

        loadData();
    </script>
</body>
</html>"""


def start_review_server(
    batch_dir: Path,
    report_dir: Optional[Path] = None,
    port: int = 8080,
    host: str = "127.0.0.1",
) -> None:
    """Launch lightweight HTTP server for interactive human QA review.

    Binds to 127.0.0.1 (localhost only) by default. Pass host='0.0.0.0'
    explicitly to expose on the local network.
    """
    if not (1 <= port <= 65535):
        raise ValueError(f"Port must be between 1 and 65535, got {port}.")

    batch_path = batch_dir.expanduser().resolve()
    if not report_dir:
        report_path = batch_path.parent / "audit-report"
    else:
        report_path = report_dir.expanduser().resolve()

    report_path.mkdir(parents=True, exist_ok=True)
    decisions_file = report_path / "review_decisions.json"

    class _Handler(ReviewRequestHandler):
        pass

    _Handler.batch_dir = batch_path
    _Handler.report_dir = report_path
    _Handler.decisions_file = decisions_file

    class _ReuseServer(socketserver.TCPServer):
        allow_reuse_address = True

    with _ReuseServer((host, port), _Handler) as httpd:
        httpd.serve_forever()
