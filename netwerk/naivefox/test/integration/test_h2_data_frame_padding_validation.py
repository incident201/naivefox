#!/usr/bin/env python3

import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location(
    "h2_data_frame_padding_validation",
    os.path.join(HERE, "h2_data_frame_padding_validation.py"),
)
VALIDATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATION)


def event(
    frame_type,
    stream_id,
    source_port,
    destination_port,
    *,
    method=None,
    status=None,
    headers=(),
    padded=False,
    padding_length=None,
    tcp_stream=0,
):
    return {
        "tcp_stream": tcp_stream,
        "source_port": source_port,
        "destination_port": destination_port,
        "type": frame_type,
        "stream_id": stream_id,
        "methods": [] if method is None else [method],
        "statuses": [] if status is None else [status],
        "headers": list(headers),
        "padded": padded,
        "padding_length": padding_length,
    }


def valid_events(padded_count=8):
    proxy_port = 4433
    request_marker = "~9" + "!" * 14
    response_marker = "~9" + "!" * 28
    rows = [
        event(
            1,
            1,
            50000,
            proxy_port,
            method="CONNECT",
            headers=(("padding", request_marker),),
        ),
        event(
            1,
            1,
            proxy_port,
            50000,
            status="200",
            headers=(("padding", response_marker),),
        ),
        event(0, 3, proxy_port, 50000),
    ]
    rows.extend(
        event(
            0,
            1,
            proxy_port,
            50000,
            padded=True,
            padding_length=index,
        )
        for index in range(padded_count)
    )
    return rows


class H2DataFramePaddingValidationTests(unittest.TestCase):
    def test_accepts_scoped_bounded_padding(self):
        result = VALIDATION.validate_events(valid_events(), 4433)
        self.assertEqual(result["connect_streams"], 1)
        self.assertEqual(result["padded_data_frames"], 8)

    def test_rejects_missing_or_excess_padding(self):
        with self.assertRaisesRegex(ValueError, "expected 1..8"):
            VALIDATION.validate_events(valid_events(0), 4433)
        with self.assertRaisesRegex(ValueError, "expected 1..8"):
            VALIDATION.validate_events(valid_events(9), 4433)

    def test_rejects_padding_outside_connect_response(self):
        rows = valid_events()
        rows.append(event(0, 3, 4433, 50000, padded=True, padding_length=7))
        with self.assertRaisesRegex(ValueError, "escaped"):
            VALIDATION.validate_events(rows, 4433)

    def test_rejects_unmarked_negotiation(self):
        rows = valid_events()
        rows[0]["headers"] = (("padding", "!" * 16),)
        with self.assertRaisesRegex(ValueError, "request lacks"):
            VALIDATION.validate_events(rows, 4433)
        rows = valid_events()
        rows[1]["headers"] = ()
        with self.assertRaisesRegex(ValueError, "response lacks"):
            VALIDATION.validate_events(rows, 4433)

    def test_parses_each_pdml_h2_protocol_instance(self):
        document = b"""<?xml version='1.0'?>
<pdml><packet>
  <proto name='tcp'>
    <field name='tcp.stream' show='4'/>
    <field name='tcp.srcport' show='50000'/>
    <field name='tcp.dstport' show='4433'/>
  </proto>
  <proto name='http2'>
    <field name='http2.type' show='1'/>
    <field name='http2.streamid' show='1'/>
    <field name='http2.headers.method' show='CONNECT'/>
    <field name='http2.header'>
      <field name='http2.header.name' show='padding'/>
      <field name='http2.header.value' show='~9!!!!!!!!!!!!!!'/>
    </field>
    <field name='http2.flags.padded' show='False'/>
  </proto>
</packet></pdml>"""
        rows = VALIDATION.parse_pdml(document)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tcp_stream"], 4)
        self.assertEqual(rows[0]["methods"], ["CONNECT"])
        self.assertEqual(rows[0]["headers"], [("padding", "~9!!!!!!!!!!!!!!")])
        self.assertFalse(rows[0]["padded"])


if __name__ == "__main__":
    unittest.main()
