#!/usr/bin/env python3
"""
OG Checker - Windows app.

Opens the checker in its own native window (Microsoft Edge WebView2, built into Windows 10 and 11).
No browser tab, no console. Closing the window quits the app.

If WebView2 is missing (very old Windows 10), it falls back to opening the page in the browser.

Run from source:  pip install -r requirements.txt  then  python app.py
"""

import os
import socket
import sys
import threading
import webbrowser

import server as srv

TITLE = "OG Checker"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Api:
    """Functions the page can call through window.pywebview.api.*"""

    def __init__(self):
        self._window = None  # leading underscore: not exposed to the page

    def save_file(self, filename, text):
        import webview
        kind = getattr(webview, "SAVE_DIALOG", None)
        if kind is None:  # pywebview 6+
            kind = webview.FileDialog.SAVE
        result = self._window.create_file_dialog(kind, save_filename=filename,
                                                 file_types=("Text files (*.txt)",))
        if not result:
            return False
        path = result[0] if isinstance(result, (list, tuple)) else result
        if not path.lower().endswith(".txt"):
            path += ".txt"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return True


def browser_fallback(url):
    """No WebView2 available: behave like before - open the page in the default browser."""
    srv.EMBEDDED[0] = False
    webbrowser.open(url.replace("?app=1", ""))
    try:
        srv.SERVER[0].serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    port = free_port()
    srv.EMBEDDED[0] = True
    httpd = srv.make_server("127.0.0.1", port)
    url = f"http://127.0.0.1:{port}/?app=1"

    try:
        import webview
    except Exception:
        return browser_fallback(url)

    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    api = Api()
    try:
        window = webview.create_window(
            TITLE, url, js_api=api,
            width=1320, height=860, min_size=(1060, 680),
            background_color="#0a0a0a",
        )
        api._window = window
        webview.start(gui="edgechromium")  # never fall back to the old Internet Explorer engine
    except Exception:
        # WebView2 runtime missing or failed: keep the app usable in the browser
        httpd.shutdown()
        httpd.server_close()
        srv.make_server("127.0.0.1", port)
        return browser_fallback(url)
    finally:
        srv.shutdown()
    os._exit(0)  # make sure no checker threads keep the process alive after the window closes


if __name__ == "__main__":
    main()
