import http.server
import socketserver
import json
import os
import threading
from urllib.parse import urlparse, parse_qs
from scraper import run_scraper, load_config, save_config, search_tmdb, update_movie_match

PORT = 5000
SCAN_STATUS = {"running": False, "message": "Idle", "current": 0, "total": 0}

def async_scan_worker():
    global SCAN_STATUS
    SCAN_STATUS = {"running": True, "message": "Starting scan...", "current": 0, "total": 0}

    def update_progress(current, total, msg):
        SCAN_STATUS["current"] = current
        SCAN_STATUS["total"] = total
        SCAN_STATUS["message"] = msg

    try:
        run_scraper(progress_callback=update_progress)
        SCAN_STATUS = {"running": False, "message": "Scan complete", "current": 0, "total": 0}
    except Exception as e:
        SCAN_STATUS = {"running": False, "message": f"Scan failed: {str(e)}", "current": 0, "total": 0}

class HuskVaultHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # Enable CORS for local testing
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/status':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(SCAN_STATUS).encode('utf-8'))
        elif parsed.path == '/api/config':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(load_config()).encode('utf-8'))
        elif parsed.path == '/api/tmdb_search':
            query = parse_qs(parsed.query).get('q', [''])[0]
            results = search_tmdb(query)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(results).encode('utf-8'))
        else:
            return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get('Content-Length', 0))
        post_body = self.rfile.read(content_length)

        if parsed.path == '/api/scan':
            global SCAN_STATUS
            if not SCAN_STATUS["running"]:
                thread = threading.Thread(target=async_scan_worker, daemon=True)
                thread.start()
                self.send_response(202)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "started"}).encode('utf-8'))
            else:
                self.send_response(409)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "already_running"}).encode('utf-8'))

        elif parsed.path == '/api/config':
            try:
                payload = json.loads(post_body)
                save_config(payload)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "saved"}).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

        elif parsed.path == '/api/fix_match':
            try:
                payload = json.loads(post_body)
                target_file = payload.get("original_file")
                tmdb_id = payload.get("tmdb_id")
                result = update_movie_match(target_file, tmdb_id)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

if __name__ == "__main__":
    # Ensure current directory files are served (index.html, catalog.json, posters/)
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), HuskVaultHandler) as httpd:
        print(f"[+] HuskVault UI server online at: http://localhost:{PORT}")
        httpd.serve_forever()
