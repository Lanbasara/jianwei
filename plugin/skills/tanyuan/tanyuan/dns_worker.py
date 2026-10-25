"""Resolver process so a stuck system resolver cannot exceed download deadlines."""
import json
import socket
import sys

try:
    records = socket.getaddrinfo(sys.argv[1], 443, type=socket.SOCK_STREAM)
    print(json.dumps(list(dict.fromkeys(record[4][0] for record in records))))
except OSError:
    raise SystemExit(1)
