#!/usr/bin/env python3

import argparse
import signal
import socket


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--ready", required=True)
    parser.add_argument("--accepted", required=True)
    args = parser.parse_args()

    running = True

    def stop(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", args.port))
        listener.listen()
        listener.settimeout(0.2)
        open(args.ready, "x", encoding="ascii").close()
        while running:
            try:
                connection, _address = listener.accept()
            except TimeoutError:
                continue
            with connection, open(args.accepted, "a", encoding="ascii") as log:
                log.write("accepted\n")


if __name__ == "__main__":
    main()
