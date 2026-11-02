"""Loopback-only synthetic browser fixture; never proxies Home Assistant."""
from argparse import ArgumentParser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/fixtures/scoped-registry.js":
            data = self.server.polyfill.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()

    def list_directory(self, path):
        self.send_error(404)


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--polyfill", required=True, type=Path)
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    if not args.polyfill.is_file():
        parser.error("Provide the separately verified npm 0.0.10 polyfill fixture")
    root = Path(__file__).resolve().parents[1]
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(root)))
    server.polyfill = args.polyfill
    print(f"Synthetic fixtures only: http://127.0.0.1:{args.port}/tools/frontend_registration_smoke.html", flush=True)
    server.serve_forever()
