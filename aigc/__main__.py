import argparse
import json
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import uvicorn

from .app import app


def main():
    parser = argparse.ArgumentParser(description="AIGC local workbench")
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8765, help="Local port for the manager")
    options = parser.parse_args()
    if not 1024 <= options.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    url = f"http://127.0.0.1:{options.port}"
    if options.open_browser:
        try:
            with urllib.request.urlopen(url + "/api/status", timeout=2) as response:
                if "session" in json.load(response):
                    webbrowser.open(url)
                    return
        except (urllib.error.URLError, TimeoutError, ValueError):
            pass
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=options.port, access_log=False))
    app.state.shutdown = lambda: setattr(server, "should_exit", True)

    def open_when_ready():
        for _ in range(100):
            if server.started:
                webbrowser.open(url)
                return
            if server.should_exit:
                return
            time.sleep(0.1)

    if options.open_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
