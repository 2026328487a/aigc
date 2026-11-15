"""Task-local OpenSSH ProxyCommand through the existing local HTTP proxy."""
import os
import socket
import sys
import threading

if os.name == 'nt':
    import msvcrt
    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)

host, port = sys.argv[1], int(sys.argv[2])
connection = socket.create_connection(('127.0.0.1', 7890), timeout=15)
connection.sendall(f'CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n'.encode())
response = bytearray()
while b'\r\n\r\n' not in response:
    data = connection.recv(4096)
    if not data:
        raise SystemExit('HTTP proxy closed the connection')
    response.extend(data)
    if len(response) > 65536:
        raise SystemExit('HTTP proxy response is too large')
headers, pending = bytes(response).split(b'\r\n\r\n', 1)
if headers.split(b'\r\n', 1)[0].split()[1] != b'200':
    raise SystemExit('HTTP CONNECT failed')
connection.settimeout(None)


def send_input():
    try:
        while True:
            block = os.read(sys.stdin.fileno(), 65536)
            if not block:
                connection.shutdown(socket.SHUT_WR)
                return
            connection.sendall(block)
    except OSError:
        return


threading.Thread(target=send_input, daemon=True).start()
if pending:
    sys.stdout.buffer.write(pending)
    sys.stdout.buffer.flush()
while True:
    block = connection.recv(65536)
    if not block:
        break
    sys.stdout.buffer.write(block)
    sys.stdout.buffer.flush()
connection.close()
