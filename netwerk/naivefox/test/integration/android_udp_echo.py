#!/usr/bin/env python3

import argparse
import json
import os
import socket

MESSAGE = b"naivefox-android-udp-probe"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args()

    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.settimeout(args.timeout)
    temporary = args.ready_file + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump({"port": server.getsockname()[1]}, stream, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, args.ready_file)
    try:
        payload, address = server.recvfrom(4096)
        if payload != MESSAGE:
            raise SystemExit("unexpected Android UDP preflight payload")
        server.sendto(payload, address)
    finally:
        server.close()


if __name__ == "__main__":
    main()
