import os
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
import socket, threading, time
LOG = open(os.path.join(ROOT, "_srv_bw_out.log"), "a", buffering=1)
def log(m):
    LOG.write(m + "\n"); print(m, flush=True)
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 9128)); s.listen(5)
log("LISTEN 9128")
BLOB = b"x" * 2000000
def handle(c, addr):
    t0 = time.time()
    log("CONN from %s at %.3f" % (addr, t0))
    try:
        c.settimeout(30)
        n = c.sendall(BLOB)
        log("SENT 2000000 bytes in %.3fs" % (time.time() - t0))
    except Exception as e:
        log("ERR %s" % e)
    c.close()
while True:
    c, addr = s.accept()
    threading.Thread(target=handle, args=(c, addr), daemon=True).start()
