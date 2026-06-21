#!/usr/bin/env python3
"""Minimal static server that avoids http.server's --directory arg (which triggers
a getcwd() call that fails under some sandbox configs)."""
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from functools import partial

ROOT = "/Users/fouksi/Documents/ivf-companion"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8082

os.chdir(ROOT)
Handler = partial(SimpleHTTPRequestHandler, directory=ROOT)
with ThreadingHTTPServer(("127.0.0.1", PORT), Handler) as httpd:
    print(f"Serving {ROOT} at http://127.0.0.1:{PORT}")
    httpd.serve_forever()
