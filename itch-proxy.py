#!/usr/bin/env python3
import http.server, urllib.request, urllib.parse, time, threading, os, sys
from http import HTTPStatus

CACHE_TTL = 300  # 5 minutes
RATE_LIMIT_INTERVAL = 1.5  # seconds between fetches to itch.io
cache = {}  # key -> (expiry, content, headers, status)
last_fetch = 0
lock = threading.Lock()

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/itch-embed"):
            self.handle_itch_proxy(parsed)
        else:
            # Serve static files from Website root
            super().do_GET()

    def handle_itch_proxy(self, parsed):
        global last_fetch
        qs = urllib.parse.parse_qs(parsed.query)
        game_id = qs.get("id", [None])[0] or parsed.path.split("/")[-1]
        # Also support ?id=4823129&... or /api/itch-embed/4823129?params
        if not game_id or not game_id.isdigit():
            self.send_error(400, "Missing id")
            return
        # Build upstream URL with same query params except id
        upstream_qs = {k: v[0] for k, v in qs.items() if k != "id"}
        # Preserve original itch embed params if provided, else use defaults
        # The frontend will send border_width, bg_color etc as query
        upstream_url = f"https://itch.io/embed/{game_id}"
        if upstream_qs:
            upstream_url += "?" + urllib.parse.urlencode(upstream_qs)
        else:
            # Fallback minimal
            upstream_url += "?border_width=0&bg_color=000000&fg_color=ffffff&link_color=ffffff&border_color=000000"

        cache_key = upstream_url
        now = time.time()
        # Serve from cache if fresh
        with lock:
            if cache_key in cache:
                expiry, content, headers, status = cache[cache_key]
                if now < expiry:
                    self.send_response(status)
                    for k, v in headers.items():
                        # Filter hop-by-hop
                        if k.lower() in ("content-encoding", "transfer-encoding", "connection", "keep-alive", "content-length"):
                            continue
                        self.send_header(k, v)
                    self.send_header("X-Cache", "HIT")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return
        # Rate limit
        with lock:
            wait = RATE_LIMIT_INTERVAL - (now - last_fetch)
            if wait > 0:
                time.sleep(wait)
            last_fetch = time.time()

        # Fetch upstream
        try:
            req = urllib.request.Request(upstream_url, headers={
                "User-Agent": "ThreeHands-Proxy/1.0 (+https://threehands.dev)",
                "Accept": "text/html,application/xhtml+xml",
            })
            with urllib.request.urlopen(req, timeout=12) as resp:
                content = resp.read()
                status = resp.status
                headers = dict(resp.headers)
                # Cache successful 200 for TTL, 429 for shorter
                ttl = CACHE_TTL if status == 200 else 30
                with lock:
                    cache[cache_key] = (time.time() + ttl, content, headers, status)
                self.send_response(status)
                for k, v in headers.items():
                    if k.lower() in ("content-encoding", "transfer-encoding", "connection", "keep-alive", "content-length"):
                        continue
                    self.send_header(k, v)
                self.send_header("X-Cache", "MISS")
                self.send_header("Content-Length", str(len(content)))
                # CORS for iframe src (allow same origin)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(content)
        except urllib.error.HTTPError as e:
            body = e.read() if hasattr(e, 'read') else b""
            # Cache 429 for 30s to avoid hammering
            with lock:
                cache[cache_key] = (time.time() + 30, body, dict(e.headers), e.code)
            self.send_response(e.code)
            for k, v in e.headers.items():
                if k.lower() in ("content-encoding", "transfer-encoding", "connection", "keep-alive", "content-length"):
                    continue
                self.send_header(k, v)
            self.send_header("X-Cache", "MISS")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            if e.code == 429 and e.headers.get("Retry-After"):
                self.send_header("Retry-After", e.headers.get("Retry-After"))
            self.end_headers()
            self.wfile.write(body)
        except Exception as ex:
            self.send_error(502, f"Proxy error: {ex}")

    def log_message(self, format, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), format%args))

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    os.chdir("/home/baba/Website")
    server_address = ("0.0.0.0", port)
    httpd = http.server.ThreadingHTTPServer(server_address, Handler)
    print(f"itch-proxy + static serving on http://0.0.0.0:{port} (cache {CACHE_TTL}s, rate {RATE_LIMIT_INTERVAL}s)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
