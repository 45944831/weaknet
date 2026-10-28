import socket, threading
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", 9129)); s.listen(5)
print("LISTEN 9129", flush=True)
def handle(c):
    c.settimeout(5)
    try:
        req = c.recv(4096)
        body = b"HTTP-OK-PROXY-PATH"
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(body), body))
    except Exception:
        pass
    c.close()
while True:
    c, _ = s.accept()
    threading.Thread(target=handle, args=(c,), daemon=True).start()
