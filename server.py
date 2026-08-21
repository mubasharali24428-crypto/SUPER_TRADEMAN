import http.server
import socketserver
import os
import sys
import webbrowser
from pathlib import Path

PORT = 8080
DIRECTORY = Path(__file__).resolve().parent / "web"


class CustomHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIRECTORY), **kwargs)

    def log_message(self, format, *args):
        # Suppress noisy standard request logs
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")


def run_server():
    os.chdir(DIRECTORY)
    with socketserver.TCPServer(("", PORT), CustomHTTPHandler) as httpd:
        url = f"http://localhost:{PORT}"
        print("=" * 80)
        print(f"🚀 SUPER_TRADEMAN Web UI is live at: {url}")
        print(f"📂 Serving dashboard from: {DIRECTORY}")
        print("=" * 80)
        print("Press Ctrl+C to stop the web server.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down web server.")


if __name__ == "__main__":
    run_server()
