"""Exercise real HTTP handlers without requiring network socket access."""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class RequestSocket:
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = io.BytesIO()

    def makefile(self, *args, **kwargs):
        return self.input

    def sendall(self, data):
        self.output.write(data)


with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
    server.DATABASE = Path(directory) / 'checks.db'
    server.setup_database()
    for route in ['areas', 'summary', 'events', 'camera-sources', 'sessions', 'scan-events', 'missing']:
        sock = RequestSocket(f'GET /api/{route} HTTP/1.0\r\nHost: localhost\r\n\r\n'.encode())
        server.ParkingHandler(sock, ('127.0.0.1', 1), None)
        headers, body = sock.output.getvalue().split(b'\r\n\r\n', 1)
        assert b'application/json' in headers, headers
        assert isinstance(json.loads(body), dict)
        assert (b'404' if route == 'missing' else b'200') in headers.splitlines()[0]
    # SQLite connection context managers do not close connections automatically.
    import gc
    gc.collect()
print('PASS: six dashboard APIs and missing-route response return valid JSON.')
