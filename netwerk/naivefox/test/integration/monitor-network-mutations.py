#!/usr/bin/env python3

import argparse
import os
import signal
import socket
import struct
import time


NETLINK_ROUTE = 0
NLMSG_NOOP = 1
NLMSG_ERROR = 2
NLMSG_DONE = 3
NLMSG_OVERRUN = 4
NLMSG_HEADER_SIZE = 16
NLMSG_ALIGNMENT = 4
RECEIVE_SIZE = 1 << 16
RTMGRP_LINK = 0x1
RTMGRP_IPV4_IFADDR = 0x10
RTMGRP_IPV4_ROUTE = 0x40
RTMGRP_IPV6_IFADDR = 0x100
RTMGRP_IPV6_ROUTE = 0x400
MUTATIONS = {
    16: "new-link",
    17: "del-link",
    20: "new-address",
    21: "del-address",
    24: "new-route",
    25: "del-route",
}


class MonitorInvalidated(RuntimeError):
    pass


def create_private(path):
    return os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
        "w",
        encoding="utf-8",
        buffering=1,
    )


def write_done_marker(path):
    directory = os.path.dirname(os.path.abspath(path))
    basename = os.path.basename(path)
    temporary = os.path.join(
        directory, f".{basename}.tmp-{os.getpid()}-{time.monotonic_ns()}"
    )
    try:
        with create_private(temporary) as marker:
            marker.write("done\n")
            marker.flush()
            os.fsync(marker.fileno())
        # Linking a completed private inode publishes the marker atomically and,
        # unlike replace(), fails closed if a stale marker already exists.
        os.link(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def parse_netlink_messages(data):
    if not data or len(data) % NLMSG_ALIGNMENT:
        raise MonitorInvalidated("invalid aligned netlink datagram framing")

    messages = []
    offset = 0
    while offset < len(data):
        remaining = len(data) - offset
        if remaining < NLMSG_HEADER_SIZE:
            raise MonitorInvalidated("truncated netlink message header")
        length, message_type, flags, sequence, process = struct.unpack_from(
            "=IHHII", data, offset
        )
        if length < NLMSG_HEADER_SIZE:
            raise MonitorInvalidated("invalid netlink message length")
        aligned_length = (length + NLMSG_ALIGNMENT - 1) & ~(NLMSG_ALIGNMENT - 1)
        if length > remaining or aligned_length > remaining:
            raise MonitorInvalidated("truncated netlink message payload")
        if message_type == NLMSG_OVERRUN:
            raise MonitorInvalidated("netlink receive queue overrun")
        if message_type == NLMSG_ERROR:
            raise MonitorInvalidated("netlink reported NLMSG_ERROR")
        messages.append((message_type, flags, sequence, process))
        offset += aligned_length

    if offset != len(data):
        raise MonitorInvalidated("invalid netlink message alignment")
    return messages


def receive_datagram(monitor):
    data, _ancillary, message_flags, _address = monitor.recvmsg(RECEIVE_SIZE)
    if message_flags & socket.MSG_TRUNC:
        raise MonitorInvalidated("truncated netlink datagram")
    return data


def record_datagram(data, events, started):
    for message_type, flags, sequence, process in parse_netlink_messages(data):
        name = MUTATIONS.get(message_type)
        if name:
            elapsed = time.monotonic_ns() - started
            events.write(
                f"elapsed_ns={elapsed} type={name} "
                f"sequence={sequence} process={process} flags={flags}\n"
            )


def drain_pending(monitor, events, started):
    monitor.setblocking(False)
    while True:
        try:
            data = receive_datagram(monitor)
        except BlockingIOError:
            return
        record_datagram(data, events, started)


def run_monitor(monitor, events, stop_requested):
    started = time.monotonic_ns()
    monitor.settimeout(0.2)
    while not stop_requested():
        try:
            data = receive_datagram(monitor)
        except socket.timeout:
            continue
        except InterruptedError:
            continue
        record_datagram(data, events, started)
    drain_pending(monitor, events, started)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--done", required=True)
    args = parser.parse_args()

    groups = (
        RTMGRP_LINK
        | RTMGRP_IPV4_IFADDR
        | RTMGRP_IPV4_ROUTE
        | RTMGRP_IPV6_IFADDR
        | RTMGRP_IPV6_ROUTE
    )
    stop = False

    def request_stop(_signal_number, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_ROUTE) as monitor:
        monitor.bind((0, groups))
        with create_private(args.events) as events:
            with create_private(args.ready) as ready:
                ready.write("ready\n")
            run_monitor(monitor, events, lambda: stop)

    write_done_marker(args.done)


if __name__ == "__main__":
    main()
