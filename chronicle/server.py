"""A loopback-only, read-only API and static evidence inspector.

No uploads, remote assets, external fonts, telemetry, or arbitrary file serving.
The input corpus is rendered as text, never as HTML or executable Markdown.
"""
from __future__ import annotations

from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import time
from urllib.parse import parse_qs, urlsplit

from .engine import KnowledgeSystem, describe

ASSETS = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}


def make_server(ks: KnowledgeSystem, port: int = 8765, demo: bool = False, ingest_seconds: float = 0) -> ThreadingHTTPServer:
    assets = Path(__file__).with_name("web")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Questions can contain sensitive information; don't log request URLs.
            pass

        def reply(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def json(self, result, status=200):
            self.reply(status, json.dumps(result, ensure_ascii=False).encode(), "application/json")

        def do_GET(self):
            expected_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in expected_hosts:
                self.json({"error": "Only the local inspector origin is allowed."}, 403)
                return
            url = urlsplit(self.path)
            params = parse_qs(url.query)
            if len(self.path) > 14000:
                self.json({"error": "Request is too long."}, 414)
                return
            try:
                if url.path in ASSETS:
                    name, mime = ASSETS[url.path]
                    self.reply(200, (assets / name).read_bytes(), mime)
                elif url.path == "/api/status":
                    snapshots = ks.store.snapshots()
                    self.json({"snapshots": snapshots, "demo": demo, "ingest_seconds": round(ingest_seconds, 3),
                               "offers": len(ks.store.offers(snapshots[-1]["id"]))})
                elif url.path in ("/api/answer", "/api/timeline"):
                    question = params.get("q", [""])[0]
                    if not question.strip() or len(question) > 8000:
                        self.json({"error": "Enter a question of at most 8000 characters."}, 400)
                        return
                    start = time.perf_counter()
                    result = ks.explain(question, params.get("as_of", [None])[0]).to_dict() if url.path == "/api/answer" else ks.timeline(question)
                    self.json({"result": result, "elapsed_ms": round((time.perf_counter() - start) * 1000, 2)})
                elif url.path == "/api/conflicts":
                    self.json([asdict(c) for c in ks.conflicts()])
                elif url.path == "/api/changes":
                    sid = params.get("snapshot", [ks.store.at(None)["id"]])[0]
                    changes = ks.store.changes(sid)
                    changed = set(changes["offer_keys_changed"])
                    details = {}
                    for offer in ks.store.offers(sid):
                        if offer.key in changed:
                            details.setdefault(offer.key, {"topic": describe(offer).split(":")[0], "values": []})
                            if offer.measures not in details[offer.key]["values"]:
                                details[offer.key]["values"].append(offer.measures)
                    changes["changed_offers"] = list(details.values())
                    self.json(changes)
                else:
                    self.json({"error": "Not found."}, 404)
            except ValueError as exc:
                self.json({"error": str(exc)}, 400)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(ks: KnowledgeSystem, port: int, demo: bool, ingest_seconds: float) -> None:
    server = make_server(ks, port, demo, ingest_seconds)
    print(f"Chronicle is ready at http://127.0.0.1:{server.server_port} — {'fictional demo' if demo else 'private local data'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
