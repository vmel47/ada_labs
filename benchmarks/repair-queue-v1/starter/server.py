"""Standard local HTTP adapter for a benchmark handle_request implementation."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--data', default='.runtime/repairs.sqlite')
    args = parser.parse_args()
    class Handler(BaseHTTPRequestHandler):
        def handle_http(self):
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 <= size <= 2_000_000:
                    return self.send_error(413)
                status, headers, body = app.handle_request(self.command, self.path,
                    self.rfile.read(size), dict(self.headers), args.data)
            except Exception:
                status, headers, body = 500, {'Content-Type':'application/json'}, b'{"error":"Internal server error"}'
            self.send_response(status)
            for key, value in headers.items():
                if key.lower() not in ('content-length','transfer-encoding','connection'):
                    self.send_header(key, value)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        do_GET = do_POST = do_PUT = do_DELETE = handle_http
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
