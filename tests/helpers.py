import socket

_open = []


def connect(port):
    sock = socket.create_connection(("127.0.0.1", port), timeout=2)
    f = sock.makefile("rwb")
    _open.append((sock, f))
    return sock, f


def query(f, text):
    f.write(text.encode() + b"\n")
    f.flush()
    return f.readline().decode().rstrip("\n")


def close_all():
    while _open:
        sock, f = _open.pop()
        f.close()
        sock.close()


BASIC = """
instruments:
  psu: {type: psu, port: 0}
  dmm: {type: dmm, port: 0, noise: 0}
  mux: {type: mux, port: 0}
loads:
  main: {type: resistor, ohms: 24}
wiring:
  - psu.ch1 -> main
  - mux.101 -> main
  - mux.common -> dmm.input
"""
