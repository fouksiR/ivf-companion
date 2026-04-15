import http.server, socketserver, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
handler = http.server.SimpleHTTPRequestHandler
httpd = socketserver.TCPServer(('', 8082), handler)
print("Serving on port 8082")
httpd.serve_forever()
