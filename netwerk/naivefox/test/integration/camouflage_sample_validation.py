#!/usr/bin/env python3

import argparse
import json
import os
import re

PREAMBLE_RESULT = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble result=(?P<result>\S+) "
    r"status=0x[0-9a-fA-F]+ "
    r"http=(?P<http>\d+) bytes=(?P<bytes>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
OPTIMISTIC_LOCAL_REPLY = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Local optimistic reply "
    r"phase=(?P<phase>queued|reply-flushed-before-outer|outer-established|"
    r"pump-started|outer-failed) "
    r"listener=(?P<listener>socks|http-connect)$"
)
ROOT_OVERLAP_ADMISSION = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble root-overlap admission=(?P<admission>\S+) "
    r"root_done=(?P<root_done>[01]) "
    r"started_resources=(?P<started_resources>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
ROOT_OVERLAP_DRAIN = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble root-overlap drain=complete "
    r"completed_resources=(?P<completed_resources>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
RESOURCE_COMMITTED_ADMISSION = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble resource-committed-overlap admission=(?P<admission>\S+) "
    r"root_done=(?P<root_done>[01]) "
    r"started_resources=(?P<started_resources>\d+) "
    r"committed_resources=(?P<committed_resources>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
RESOURCE_COMMITTED_DRAIN = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble resource-committed-overlap drain=complete "
    r"completed_resources=(?P<completed_resources>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
RESOURCE_NATIVE_CACHE_COMMITTED_ADMISSION = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble resource-native-cache-committed-overlap "
    r"admission=(?P<admission>\S+) "
    r"root_done=(?P<root_done>[01]) "
    r"started_resources=(?P<started_resources>\d+) "
    r"committed_resources=(?P<committed_resources>\d+) "
    r"cache_new=(?P<cache_new>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
RESOURCE_NATIVE_CACHE_COMMITTED_DRAIN = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble resource-native-cache-committed-overlap drain=complete "
    r"completed_resources=(?P<completed_resources>\d+) "
    r"cache_new=(?P<cache_new>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_PRELOAD_DISCOVERY = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-preload "
    r"parser=html5-speculative-scanner parsers=(?P<parsers>\d+) "
    r"descriptors=(?P<descriptors>\d+) provenance=(?P<provenance>\S+) "
    r"internal_type=(?P<internal_type>\d+) protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_PRELOAD_CHANNEL = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-preload channel=async-open "
    r"channels=(?P<channels>\d+) protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_PRELOAD_ADMISSION = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-preload admission=(?P<admission>\S+) "
    r"root_done=(?P<root_done>[01]) "
    r"started_resources=(?P<started_resources>\d+) "
    r"committed_resources=(?P<committed_resources>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_PRELOAD_BARRIER = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-preload barrier=released "
    r"protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_PRELOAD_DRAIN = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-preload drain=complete "
    r"completed_resources=(?P<completed_resources>\d+) "
    r"http=(?P<http>\d+) protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_DOCUMENT_START_ADMISSION = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-document-start "
    r"admission=(?P<admission>\S+) "
    r"request_committed=(?P<request_committed>[01]) "
    r"root_done=(?P<root_done>[01]) "
    r"protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_NAVIGATION_STOP_ADMISSION = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-document-start-navigation-stop "
    r"admission=(?P<admission>\S+) "
    r"request_committed=(?P<request_committed>[01]) "
    r"root_done=(?P<root_done>[01]) protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_NAVIGATION_STOP_STYLESHEET_COMMITTED = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-document-start-navigation-stop "
    r"phase=stylesheet-committed stream=(?P<stream>\d+) "
    r"status=(?P<status>\S+) protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_NAVIGATION_STOP_TUNNEL_ACTIVE = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-document-start-navigation-stop "
    r"phase=tunnel-application-active "
    r"direction=(?P<direction>\S+) "
    r"bytes_positive=(?P<bytes_positive>[01]) "
    r"protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_NAVIGATION_STOP_RESPONSE_STARTED = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-document-start-navigation-stop "
    r"phase=stylesheet-response-started http=(?P<http>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_NAVIGATION_STOP_ISSUED = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-document-start-navigation-stop "
    r"phase=navigation-stop-issued reason=(?P<reason>\S+) "
    r"load_group=(?P<load_group>\S+) protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_NAVIGATION_STOP_ONSTOP = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-document-start-navigation-stop "
    r"phase=stylesheet-onstop status=(?P<status>\S+) "
    r"expected=(?P<expected>[01]) protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_NAVIGATION_STOP_DRAIN = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-document-start-navigation-stop drain=complete "
    r"root_done=(?P<root_done>[01]) css_committed=(?P<css_committed>[01]) "
    r"css_aborted=(?P<css_aborted>[01]) http=(?P<http>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_RESPONSE_STOP_ADMISSION = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-document-start-response-stop "
    r"admission=(?P<admission>\S+) "
    r"request_committed=(?P<request_committed>[01]) "
    r"root_done=(?P<root_done>[01]) protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_RESPONSE_STOP_STYLESHEET_COMMITTED = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-document-start-response-stop "
    r"phase=stylesheet-committed stream=(?P<stream>\d+) "
    r"status=(?P<status>\S+) protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_RESPONSE_STOP_TUNNEL_ACTIVE = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-document-start-response-stop "
    r"phase=tunnel-application-active direction=(?P<direction>\S+) "
    r"bytes_positive=(?P<bytes_positive>[01]) payload=(?P<payload>\S+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_RESPONSE_STOP_RESPONSE_STARTED = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-document-start-response-stop "
    r"phase=stylesheet-response-started http=(?P<http>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_RESPONSE_STOP_ISSUED = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-document-start-response-stop "
    r"phase=navigation-stop-issued reason=(?P<reason>\S+) "
    r"load_group=(?P<load_group>\S+) protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_RESPONSE_STOP_ONSTOP = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-document-start-response-stop "
    r"phase=stylesheet-onstop status=(?P<status>\S+) "
    r"expected=(?P<expected>[01]) protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_RESPONSE_STOP_DRAIN = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-document-start-response-stop drain=complete "
    r"root_done=(?P<root_done>[01]) css_committed=(?P<css_committed>[01]) "
    r"css_aborted=(?P<css_aborted>[01]) "
    r"css_completed=(?P<css_completed>[01]) http=(?P<http>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_DOCUMENT_HANDOFF = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-document-handoff phase=(?P<phase>\S+)"
    r"(?: delivery=(?P<delivery>\S+))? "
    r"protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_DOCUMENT_HANDOFF_PHASES = (
    "root-response-validated",
    "handoff-suspend",
    "consumer-constructed-main",
    "replacement-listener-installed",
    "handoff-resume",
    "first-parser-feed",
)
NATIVE_PARSER_RETARGET = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-retarget phase=(?P<phase>\S+)"
    r"(?: target=(?P<target>\S+) verified=(?P<verified>[01]))?"
    r"(?: delivery=(?P<delivery>\S+))? "
    r"protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_RETARGET_PHASES = (
    "root-response-validated",
    "handoff-suspend",
    "consumer-constructed-main",
    "delivery-retargeted",
    "replacement-listener-installed",
    "handoff-resume",
    "first-parser-feed",
    "parser-data-finished",
)
NATIVE_PARSER_ROOT_RENDEZVOUS_RETARGET_PHASES = (
    "delivery-retargeted",
    "first-parser-feed",
    "parser-data-finished",
)
NATIVE_PARSER_ROOT_REPLACEMENT = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-root-replacement phase=(?P<phase>[a-z-]+) "
    r"channel=(?P<channel>\d+) request=(?P<request>\d+) "
    r"generation=(?P<generation>\d+) protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_ROOT_REPLACEMENT_PHASES = (
    "root-response-validated",
    "physical-root-suspended",
    "replacement-registered",
    "connect-parent-same-root-linked",
    "redirect-verifier-run-queued",
    "redirect-verifier-run",
    "redirect-verifier-callback-queued",
    "redirect-verifier-callback-resolved",
    "replacement-listener-published",
    "forward-on-start-sent",
    "physical-root-resume",
    "forward-on-start-received",
    "consumer-constructed-main",
    "logical-request-retargeted",
)
NATIVE_STYLE_ACTIVATION = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Native style activation "
    r"phase=(?P<phase>descriptor-frozen|request-primary-actor-created|"
    r"request-primary-actor-bound|child-open-sent|background-dispatched|"
    r"request-background-actor-created|request-background-actor-bound|"
    r"bg-ready-sent|parent-channel-created|background-ready-received|"
    r"activation-released|async-open|on-stop|"
    r"request-primary-actor-delete-sent|"
    r"request-background-actor-delete-sent|"
    r"request-primary-actor-destroyed|"
    r"request-background-actor-destroyed) "
    r"request=(?P<request>\d+)"
    r"(?: status=(?P<status>0x[0-9a-fA-F]+))?$"
)
NATIVE_STYLE_ACTIVATION_PHASES = {
    "descriptor-frozen",
    "request-primary-actor-created",
    "request-primary-actor-bound",
    "child-open-sent",
    "background-dispatched",
    "request-background-actor-created",
    "request-background-actor-bound",
    "bg-ready-sent",
    "parent-channel-created",
    "background-ready-received",
    "activation-released",
    "async-open",
    "on-stop",
    "request-primary-actor-delete-sent",
    "request-background-actor-delete-sent",
    "request-primary-actor-destroyed",
    "request-background-actor-destroyed",
}
NATIVE_STYLE_CHANNEL_CREATED = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Preamble native-parser-preload "
    r"lifecycle=stylesheet-channel-created stream=1 "
    r"activation=ipc-rendezvous protocol=h3$"
)
NATIVE_STYLE_OPENED = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Preamble native-parser-preload "
    r"lifecycle=stylesheet-opened stream=1 kind=from-parser "
    r"referrer=inherited activation=ipc-rendezvous protocol=h3$"
)
NATIVE_ROOT_PLAIN_PHASES = {
    "descriptor-frozen",
    "request-primary-actor-created",
    "request-primary-actor-bound",
    "begin-sent",
    "begin-received",
    "connect-parent-sent",
    "background-dispatched",
    "redirect-verification-started",
    "redirect-verification-queued",
    "request-background-actor-created",
    "request-background-actor-bound",
    "background-ready",
    "bg-linked",
    "continue-verification",
    "background-wait",
    "ready-to-verify",
    "setup-finished",
    "forward-start",
    "resume",
    "request-primary-actor-delete-sent",
    "request-background-actor-delete-sent",
    "request-primary-actor-destroyed",
    "request-background-actor-destroyed",
}
NATIVE_ROOT_REQUIRED_PLAIN_PHASES = NATIVE_ROOT_PLAIN_PHASES - {"background-wait"}
NATIVE_ROOT_PHASE = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Native root replacement activation "
    r"phase=(?P<phase>[a-z-]+) request=(?P<request>\d+)"
    r"(?P<suffix>.*)$"
)
DOCUMENT_OVERLAP_ADMISSION = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble document-overlap admission=(?P<admission>\S+) "
    r"response_accepted=(?P<response_accepted>[01]) "
    r"root_done=(?P<root_done>[01]) "
    r"protocol=(?P<protocol>h2|h3)$"
)
DOCUMENT_OVERLAP_DRAIN = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble document-overlap drain=complete root_done=1 "
    r"completed_resources=(?P<completed_resources>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
DOCUMENT_START_OVERLAP_ADMISSION = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble document-start-overlap admission=(?P<admission>\S+) "
    r"request_committed=(?P<request_committed>[01]) "
    r"root_done=(?P<root_done>[01]) "
    r"protocol=(?P<protocol>h2|h3)$"
)
DOCUMENT_START_OVERLAP_DRAIN = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble document-start-overlap drain=complete root_done=1 "
    r"completed_resources=(?P<completed_resources>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_RESOURCE_TREE_ADMISSION = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-resource-tree admission=(?P<admission>\S+) "
    r"request_committed=(?P<request_committed>[01]) "
    r"root_done=(?P<root_done>[01]) "
    r"protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_RESOURCE_TREE_OPEN = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Preamble native-parser-resource-tree "
    r"lifecycle=resource-(?P<lifecycle>opened|prepared) "
    r"stream=(?P<stream>\d+) "
    r"kind=(?P<kind>style|script|image) referrer=inherited "
    r"protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_RESOURCE_TREE_DEFERRED_OPEN = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Preamble native-parser-resource-tree "
    r"lifecycle=deferred-resource-opened stream=(?P<stream>\d+) "
    r"kind=image cause=next-main-turn protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_RESOURCE_TREE_COMMIT = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Preamble native-parser-resource-tree "
    r"lifecycle=resource-committed stream=(?P<stream>\d+) "
    r"status=waiting-for protocol=(?P<protocol>h2|h3)$"
)
NATIVE_PARSER_RESOURCE_TREE_DRAIN = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-parser-resource-tree drain=complete "
    r"completed_resources=(?P<completed_resources>\d+) "
    r"http=(?P<http>\d+) protocol=(?P<protocol>h2|h3)$"
)
ESTABLISHED = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"established target=\S+ outer=(?P<protocol>h2|h3) "
    r"padding=(?P<padding>yes|no)$"
)
DELAYED_PADDING_PHASE = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"diagnostic-delayed-padding-phase negotiated=1 protocol=(?P<protocol>h2) "
    r"framed-records=16 random-records=9-16$"
)
NATIVE_CACHE_OPEN = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-cache-open cache=readonly-miss "
    r"protocol=(?P<protocol>h2|h3)$"
)
NATIVE_CHANNEL_OPEN = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble native-channel-open cache=new-writable-entry "
    r"classifier=async-suspend-resume "
    r"protocol=(?P<protocol>h2|h3)$"
)
COLD_WINNER_HANDOFF = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble cold-winner-handoff "
    r"establishment=requestless-single-proxy dispatch=exact-winner "
    r"protocol=(?P<protocol>h2|h3)$"
)

PROCESS_HELLO = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Native activation process phase=hello "
    r"parent_pid=(?P<parent>\d+) child_pid=(?P<child>\d+) "
    r"cross_process=1 persistent=1$"
)
PROCESS_CHILD_RUNNING = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Native activation process phase=child-running "
    r"parent_pid=(?P<parent>\d+) child_pid=(?P<child>\d+)$"
)
PROCESS_BACKGROUND_CHILD_READY = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Native activation process "
    r"phase=background-child-ready pid=(?P<pid>\d+)$"
)
PROCESS_FULL_PHASE = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Native activation process "
    r"phase=(?P<phase>full-[a-z0-9-]+) (?P<fields>.+)$"
)
PROCESS_PARENT_PHASE = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) preamble "
    r"native-parser-(?P<mode>process|full-process) "
    r"phase=(?P<phase>[a-z0-9-]+) (?P<fields>.+) "
    r"protocol=h3$"
)
PROCESS_CHILD_PHASE = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Native activation child "
    r"phase=(?P<phase>[a-z0-9-]+) (?P<fields>.+)$"
)
PROCESS_FIELD = re.compile(r"(?P<key>[a-z_]+)=(?P<value>[^ ]+)")


def _process_fields(text):
    fields = {}
    position = 0
    for match in PROCESS_FIELD.finditer(text):
        if match.start() != position:
            raise ValueError("malformed native parser process fields")
        key = match["key"]
        if key in fields:
            raise ValueError("duplicate native parser process field")
        fields[key] = match["value"]
        position = match.end() + 1
    if not fields or position - 1 != len(text):
        raise ValueError("malformed native parser process fields")
    return fields


def _single_process_phase(markers, phase, side):
    selected = [marker for marker in markers if marker["phase"] == phase]
    if len(selected) != 1:
        raise ValueError(
            f"native parser process requires exactly one {side} {phase} marker"
        )
    return selected[0]


def validate_native_parser_process(
    log_lines, expected_connection=None, expected_mode="process"
):
    if expected_mode not in ("process", "full-process"):
        raise ValueError("unsupported native parser process validation mode")
    hellos = [
        (index, match)
        for index, line in enumerate(log_lines)
        if (match := PROCESS_HELLO.fullmatch(line)) is not None
    ]
    if len(hellos) != 1:
        raise ValueError("native parser process requires one persistent hello")
    hello_index, hello = hellos[0]
    parent_pid = int(hello["parent"])
    child_pid = int(hello["child"])
    if not parent_pid or not child_pid or parent_pid == child_pid:
        raise ValueError("native parser process did not prove distinct PIDs")
    running = [
        match
        for line in log_lines
        if (match := PROCESS_CHILD_RUNNING.fullmatch(line)) is not None
    ]
    if (
        len(running) != 1
        or int(running[0]["parent"]) != parent_pid
        or int(running[0]["child"]) != child_pid
    ):
        raise ValueError("native parser process child-running identity differs")
    background_ready = [
        (index, match)
        for index, line in enumerate(log_lines)
        if (match := PROCESS_BACKGROUND_CHILD_READY.fullmatch(line)) is not None
    ]
    if len(background_ready) != 1 or int(background_ready[0][1]["pid"]) != child_pid:
        raise ValueError("native parser process background identity differs")

    parent_markers = []
    child_markers = []
    full_markers = []
    for index, line in enumerate(log_lines):
        parent = PROCESS_PARENT_PHASE.fullmatch(line)
        if parent:
            parent_markers.append({
                "index": index,
                "connection": int(parent["connection"]),
                "mode": parent["mode"],
                "phase": parent["phase"],
                "fields": _process_fields(parent["fields"]),
            })
        child = PROCESS_CHILD_PHASE.fullmatch(line)
        if child:
            child_markers.append({
                "index": index,
                "phase": child["phase"],
                "fields": _process_fields(child["fields"]),
            })
        full = PROCESS_FULL_PHASE.fullmatch(line)
        if full:
            full_markers.append({
                "index": index,
                "phase": full["phase"],
                "fields": _process_fields(full["fields"]),
            })

    parent_fields = {
        "physical-root-suspended": {"channel", "generation", "parent_pid"},
        "root-registered": {"request", "generation", "parent_pid"},
        "root-ready-resume": {"request", "generation", "parent_pid"},
        "root-data": {
            "request",
            "generation",
            "sequence",
            "bytes",
            "parent_pid",
        },
        "root-stop": {
            "request",
            "generation",
            "sequence",
            "status",
            "parent_pid",
        },
        "style-opened": {"root", "style", "sequence", "parent_pid"},
        "parser-finished": {
            "request",
            "generation",
            "sequence",
            "bytes",
            "styles",
            "parent_pid",
        },
        "style-onstop-complete": {"style", "status", "parent_pid"},
    }
    child_fields = {
        "root-ready": {"request", "generation", "pid"},
        "root-data-accepted": {
            "request",
            "generation",
            "sequence",
            "bytes",
            "pid",
            "main_thread",
        },
        "parser-feed": {
            "request",
            "generation",
            "sequence",
            "bytes",
            "descriptors",
            "status",
            "pid",
            "main_thread",
        },
        "root-stop-accepted": {
            "request",
            "generation",
            "sequence",
            "status",
            "pid",
            "main_thread",
        },
        "parser-finish": {
            "request",
            "generation",
            "sequence",
            "bytes",
            "descriptors",
            "status",
            "pid",
            "main_thread",
        },
        "style-discovered": {"root", "generation", "style", "sequence", "pid"},
        "parser-finished": {
            "request",
            "generation",
            "sequence",
            "bytes",
            "styles",
            "status",
            "pid",
            "main_thread",
        },
        "style-complete": {
            "request",
            "root",
            "generation",
            "pid",
            "main_thread",
        },
        "style-actor-destroyed": {
            "request",
            "root",
            "generation",
            "completed",
            "reason",
            "pid",
        },
        "root-actor-destroyed": {
            "request",
            "generation",
            "finished",
            "reason",
            "pid",
        },
    }
    for marker in parent_markers:
        if marker["mode"] != expected_mode:
            raise ValueError("native parser process mode marker differs")
        expected = parent_fields.get(marker["phase"])
        if expected is None or set(marker["fields"]) != expected:
            raise ValueError("native parser process parent marker schema differs")
    for marker in child_markers:
        expected = child_fields.get(marker["phase"])
        if expected is None or set(marker["fields"]) != expected:
            raise ValueError("native parser process child marker schema differs")

    if expected_mode == "process":
        if full_markers:
            raise ValueError("partial native parser process logged full lifecycle")
    else:
        full_fields = {
            "full-root-primary-ready": {
                "request",
                "generation",
                "parent_pid",
                "child_pid",
            },
            "full-root-background-ready": {
                "request",
                "generation",
                "parent_pid",
                "child_pid",
            },
            "full-root-verification-queued": {
                "request",
                "generation",
                "parent_pid",
                "child_pid",
            },
            "full-root-verification-run": {
                "request",
                "generation",
                "parent_pid",
                "child_pid",
            },
            "full-root-onstart-forwarded": {
                "request",
                "generation",
                "parent_pid",
                "child_pid",
            },
            "full-style-primary-ready": {
                "request",
                "generation",
                "style",
                "sequence",
                "parent_pid",
                "child_pid",
            },
            "full-style-background-ready": {
                "request",
                "generation",
                "style",
                "sequence",
                "parent_pid",
                "child_pid",
            },
            "full-style-join-released": {
                "request",
                "generation",
                "style",
                "sequence",
                "parent_pid",
                "child_pid",
            },
            "full-root-background-drained": {
                "request",
                "generation",
                "canceled",
                "parent_pid",
                "child_pid",
            },
        }
        if len(full_markers) != len(full_fields):
            raise ValueError(
                "full native parser process requires one marker per join phase"
            )
        if {marker["phase"] for marker in full_markers} != set(full_fields):
            raise ValueError("full native parser process join phases differ")
        for marker in full_markers:
            if set(marker["fields"]) != full_fields[marker["phase"]]:
                raise ValueError("full native parser process marker schema differs")

    parent_single_phases = (
        "physical-root-suspended",
        "root-registered",
        "root-ready-resume",
        "root-stop",
        "style-opened",
        "parser-finished",
        "style-onstop-complete",
    )
    parent_single = {
        phase: _single_process_phase(parent_markers, phase, "parent")
        for phase in parent_single_phases
    }
    child_single_phases = (
        "root-ready",
        "root-stop-accepted",
        "parser-finish",
        "style-discovered",
        "parser-finished",
        "style-complete",
        "style-actor-destroyed",
        "root-actor-destroyed",
    )
    child_single = {
        phase: _single_process_phase(child_markers, phase, "child")
        for phase in child_single_phases
    }
    full_single = (
        {marker["phase"]: marker for marker in full_markers}
        if expected_mode == "full-process"
        else {}
    )

    connections = {marker["connection"] for marker in parent_markers}
    if len(connections) != 1:
        raise ValueError("native parser process phases span multiple connections")
    if expected_connection is not None and connections != {expected_connection}:
        raise ValueError("native parser process connection identity differs")
    if hello_index >= min(marker["index"] for marker in parent_markers):
        raise ValueError("native parser process hello followed request lifecycle")
    for marker in parent_markers:
        if int(marker["fields"]["parent_pid"]) != parent_pid:
            raise ValueError("native parser process parent PID changed")
    for marker in child_markers:
        if int(marker["fields"]["pid"]) != child_pid:
            raise ValueError("native parser process child PID changed")

    request = parent_single["root-registered"]["fields"].get("request")
    generation = parent_single["root-registered"]["fields"].get("generation")
    style = parent_single["style-opened"]["fields"].get("style")
    if (
        not request
        or not generation
        or not style
        or any(int(value) <= 0 for value in (request, generation, style))
    ):
        raise ValueError("native parser process identity is incomplete")
    channel = parent_single["physical-root-suspended"]["fields"]["channel"]
    if int(channel) <= 0:
        raise ValueError("native parser process root channel identity is invalid")
    for marker in (*parent_markers, *child_markers):
        fields = marker["fields"]
        if "request" in fields and marker["phase"] not in (
            "style-complete",
            "style-actor-destroyed",
        ):
            if marker["phase"] in ("style-onstop-complete",):
                pass
            elif fields["request"] != request:
                raise ValueError("native parser process root request ID changed")
        if "root" in fields and fields["root"] != request:
            raise ValueError("native parser process style root ID changed")
        if "generation" in fields and fields["generation"] != generation:
            raise ValueError("native parser process generation changed")
    for marker in (
        parent_single["style-opened"],
        parent_single["style-onstop-complete"],
        child_single["style-discovered"],
        child_single["style-complete"],
        child_single["style-actor-destroyed"],
    ):
        fields = marker["fields"]
        marker_style = fields.get("style", fields.get("request"))
        if marker_style != style:
            raise ValueError("native parser process style request ID changed")

    if expected_mode == "full-process":
        for marker in full_markers:
            fields = marker["fields"]
            if (
                fields["request"] != request
                or fields["generation"] != generation
                or int(fields["parent_pid"]) != parent_pid
                or int(fields["child_pid"]) != child_pid
            ):
                raise ValueError("full native parser process identity changed")
            if marker["phase"].startswith("full-style-") and (
                fields["style"] != style or fields["sequence"] != "1"
            ):
                raise ValueError("full native parser process style identity changed")
        if full_single["full-root-background-drained"]["fields"]["canceled"] != "0":
            raise ValueError("full native parser process clean route was canceled")

    parent_data = [
        marker for marker in parent_markers if marker["phase"] == "root-data"
    ]
    child_data = [
        marker for marker in child_markers if marker["phase"] == "root-data-accepted"
    ]
    parser_feeds = [
        marker for marker in child_markers if marker["phase"] == "parser-feed"
    ]
    if not parent_data or not (
        len(parent_data) == len(child_data) == len(parser_feeds)
    ):
        raise ValueError("native parser process DATA lifecycle is incomplete")
    expected_sequences = list(range(1, len(parent_data) + 1))
    for markers in (parent_data, child_data, parser_feeds):
        sequences = [int(marker["fields"].get("sequence", "0")) for marker in markers]
        if sequences != expected_sequences:
            raise ValueError("native parser process DATA sequence is not contiguous")
    for parent, accepted, feed in zip(parent_data, child_data, parser_feeds):
        if not (parent["index"] < accepted["index"] < feed["index"]):
            raise ValueError("native parser process DATA crossed phases out of order")
        sizes = {
            int(marker["fields"].get("bytes", "-1"))
            for marker in (parent, accepted, feed)
        }
        if len(sizes) != 1 or next(iter(sizes)) <= 0:
            raise ValueError("native parser process DATA byte count changed")
        if feed["fields"].get("main_thread") != "0":
            raise ValueError("native parser process parser feed ran on main thread")
        if feed["fields"].get("status") != "0x00000000":
            raise ValueError("native parser process parser feed failed")

    main_thread_phases = (
        child_single["root-stop-accepted"],
        child_single["parser-finished"],
        child_single["style-complete"],
    )
    if any(marker["fields"]["main_thread"] != "1" for marker in main_thread_phases):
        raise ValueError("native parser process child main-thread phase moved")
    if any(marker["fields"]["main_thread"] != "1" for marker in child_data):
        raise ValueError("native parser process DATA admission moved off main thread")

    stop_sequence = len(parent_data) + 1
    for marker in (
        parent_single["root-stop"],
        child_single["root-stop-accepted"],
        child_single["parser-finish"],
        child_single["parser-finished"],
        parent_single["parser-finished"],
    ):
        if int(marker["fields"].get("sequence", "0")) != stop_sequence:
            raise ValueError("native parser process STOP sequence changed")
    if child_single["parser-finish"]["fields"].get("main_thread") != "0":
        raise ValueError("native parser process parser finish ran on main thread")
    for marker in (
        parent_single["root-stop"],
        child_single["root-stop-accepted"],
        child_single["parser-finish"],
        child_single["parser-finished"],
    ):
        if marker["fields"].get("status") != "0x00000000":
            raise ValueError("native parser process terminal status is not clean")
    total_bytes = sum(int(marker["fields"]["bytes"]) for marker in parent_data)
    for marker in (
        child_single["parser-finish"],
        child_single["parser-finished"],
        parent_single["parser-finished"],
    ):
        if int(marker["fields"].get("bytes", "-1")) != total_bytes:
            raise ValueError("native parser process final byte count changed")
    if any(
        marker["fields"].get("styles") != "1"
        for marker in (
            child_single["parser-finished"],
            parent_single["parser-finished"],
        )
    ):
        raise ValueError("native parser process did not discover exactly one style")
    descriptor_sources = [
        marker
        for marker in (*parser_feeds, child_single["parser-finish"])
        if int(marker["fields"]["descriptors"]) > 0
    ]
    if (
        sum(
            int(marker["fields"]["descriptors"])
            for marker in (*parser_feeds, child_single["parser-finish"])
        )
        != 1
        or len(descriptor_sources) != 1
        or descriptor_sources[0]["index"] >= child_single["style-discovered"]["index"]
    ):
        raise ValueError("native parser process descriptor provenance is invalid")
    if (
        child_single["style-discovered"]["fields"].get("sequence") != "1"
        or parent_single["style-opened"]["fields"].get("sequence") != "1"
    ):
        raise ValueError("native parser process discovery sequence is invalid")
    if parent_single["style-onstop-complete"]["fields"]["status"] != "0x00000000":
        raise ValueError("native parser process style completion failed")
    if (
        child_single["style-actor-destroyed"]["fields"].get("completed") != "1"
        or child_single["style-actor-destroyed"]["fields"].get("reason") != "1"
    ):
        raise ValueError("native parser process style actor died before completion")
    if (
        child_single["root-actor-destroyed"]["fields"].get("finished") != "1"
        or child_single["root-actor-destroyed"]["fields"].get("reason") != "1"
    ):
        raise ValueError("native parser process root actor died before parser finish")

    ordered = (
        parent_single["physical-root-suspended"]["index"],
        parent_single["root-registered"]["index"],
        child_single["root-ready"]["index"],
        parent_single["root-ready-resume"]["index"],
        parent_data[0]["index"],
        parent_single["root-stop"]["index"],
        child_single["root-stop-accepted"]["index"],
        child_single["parser-finish"]["index"],
        child_single["parser-finished"]["index"],
        parent_single["parser-finished"]["index"],
        parent_single["style-onstop-complete"]["index"],
        child_single["style-complete"]["index"],
        child_single["style-actor-destroyed"]["index"],
    )
    if tuple(sorted(ordered)) != ordered:
        raise ValueError("native parser process lifecycle ordering is invalid")
    if not (
        child_single["style-discovered"]["index"]
        < parent_single["style-opened"]["index"]
        < parent_single["parser-finished"]["index"]
        < parent_single["style-onstop-complete"]["index"]
    ):
        raise ValueError("native parser process style lifecycle ordering is invalid")
    if not (
        child_single["parser-finished"]["index"]
        < parent_single["parser-finished"]["index"]
        and child_single["parser-finished"]["index"]
        < child_single["root-actor-destroyed"]["index"]
    ):
        raise ValueError(
            "native parser process root actor teardown ordering is invalid"
        )
    if expected_mode == "full-process":
        root_primary = full_single["full-root-primary-ready"]["index"]
        root_background = full_single["full-root-background-ready"]["index"]
        verification_queued = full_single["full-root-verification-queued"]["index"]
        verification_run = full_single["full-root-verification-run"]["index"]
        onstart_forwarded = full_single["full-root-onstart-forwarded"]["index"]
        style_primary = full_single["full-style-primary-ready"]["index"]
        style_background = full_single["full-style-background-ready"]["index"]
        style_released = full_single["full-style-join-released"]["index"]
        background_drained = full_single["full-root-background-drained"]["index"]
        if not (
            parent_single["root-registered"]["index"]
            < min(root_primary, root_background)
            and max(root_primary, root_background)
            < verification_queued
            < verification_run
            < onstart_forwarded
            < parent_single["root-ready-resume"]["index"]
        ):
            raise ValueError("full native parser root join ordering is invalid")
        if not (
            max(style_primary, style_background)
            < style_released
            < parent_single["style-opened"]["index"]
        ):
            raise ValueError("full native parser style join ordering is invalid")
        if not (
            parent_single["parser-finished"]["index"] < background_drained
            and parent_single["style-onstop-complete"]["index"] < background_drained
        ):
            raise ValueError(
                "full native parser background actors did not drain terminally"
            )
    forbidden = (
        "consumer-constructed-main",
        "native-parser-retarget phase=",
        "native-parser-root-replacement phase=",
    )
    if any(token in line for token in forbidden for line in log_lines):
        raise ValueError("native parser process used a forbidden parent parser path")


def validate_sample(arm, protocol, log_text, feature_document):
    log_lines = log_text.splitlines()
    supported_arms = (
        "off",
        "gate",
        "root",
        "root-pmtud-control",
        "document-complete",
        "document-carrier-dispatch",
        "document-cold-winner-handoff",
        "document-native-cache-open",
        "document-native-channel-open",
        "document-handshake-confirmed",
        "document-first-buffer-overlap",
        "document-first-buffer-task-overlap",
        "document-first-buffer-task-delayed-padding",
        "document-first-buffer-task-optimistic",
        "document-first-buffer-task-http-connect",
        "document-first-buffer-http-connect",
        "document-first-buffer-http-connect-delayed-padding",
        "document-first-buffer-http-connect-optimistic",
        "document-overlap",
        "document-headers-task-overlap",
        "document-headers-task-http-connect",
        "document-overlap-http-connect",
        "document-start-http-connect",
        "document-start-overlap",
        "document-start-task-overlap",
        "document-start-task-http-connect",
        "tree-complete",
        "tree-complete-css",
        "tree-complete-resource-tree",
        "tree-early-overlap",
        "tree-early-overlap-resource-tree",
        "tree-root-overlap",
        "tree-root-overlap-css",
        "tree-resource-committed-overlap-css",
        "tree-resource-committed-overlap-tree",
        "tree-resource-committed-overlap-page",
        "tree-resource-native-cache-committed-overlap",
        "tree-native-parser-preload-overlap-css",
        "tree-native-parser-document-start-overlap-css",
        "tree-native-parser-document-start-resource-tree",
        "tree-native-parser-resource-committed-tree",
        "tree-native-parser-resource-committed-page",
        "tree-native-parser-resource-committed-page-http-connect",
        "tree-native-parser-document-start-navigation-stop-css",
        "tree-native-parser-document-start-response-stop-css",
        "tree-native-parser-document-handoff-overlap-css",
        "tree-native-parser-retarget-overlap-css",
        "tree-native-parser-ipc-rendezvous-overlap-css",
        "tree-native-parser-root-rendezvous-overlap-css",
        "tree-native-parser-process-overlap-css",
        "tree-native-parser-full-process-overlap-css",
        "tree-warm-css-304",
        "tree-overlap",
    )
    if arm not in supported_arms:
        raise ValueError("unsupported NaiveFox arm")
    if protocol not in ("h2", "h3"):
        raise ValueError("unsupported outer protocol")
    if arm == "root-pmtud-control" and protocol != "h3":
        raise ValueError("root-pmtud-control requires h3")
    if arm == "document-handshake-confirmed" and protocol != "h3":
        raise ValueError("document-handshake-confirmed requires h3")
    if arm == "document-carrier-dispatch" and protocol != "h3":
        raise ValueError("document-carrier-dispatch requires h3")
    if arm == "document-cold-winner-handoff" and protocol != "h3":
        raise ValueError("document-cold-winner-handoff requires h3")
    if arm == "document-native-cache-open" and protocol != "h3":
        raise ValueError("document-native-cache-open requires h3")
    if (
        arm
        in (
            "document-first-buffer-task-delayed-padding",
            "document-first-buffer-http-connect-delayed-padding",
        )
        and protocol != "h2"
    ):
        raise ValueError(f"{arm} requires h2")
    if arm == "document-native-channel-open" and protocol != "h3":
        raise ValueError("document-native-channel-open requires h3")
    if (
        arm
        in (
            "tree-resource-committed-overlap-css",
            "tree-resource-committed-overlap-tree",
            "tree-resource-committed-overlap-page",
            "tree-native-parser-resource-committed-tree",
            "tree-complete-resource-tree",
            "tree-early-overlap-resource-tree",
        )
        and protocol != "h3"
    ):
        raise ValueError(f"{arm} requires h3")
    if arm == "tree-resource-native-cache-committed-overlap" and protocol != "h3":
        raise ValueError("tree-resource-native-cache-committed-overlap requires h3")
    if arm == "tree-native-parser-preload-overlap-css" and protocol != "h3":
        raise ValueError("tree-native-parser-preload-overlap-css requires h3")
    if (
        arm == "tree-native-parser-document-start-response-stop-css"
        and protocol != "h3"
    ):
        raise ValueError(
            "tree-native-parser-document-start-response-stop-css requires h3"
        )
    if arm == "tree-native-parser-document-handoff-overlap-css" and protocol != "h3":
        raise ValueError("tree-native-parser-document-handoff-overlap-css requires h3")
    if arm == "tree-native-parser-retarget-overlap-css" and protocol != "h3":
        raise ValueError("tree-native-parser-retarget-overlap-css requires h3")
    if arm == "tree-native-parser-ipc-rendezvous-overlap-css" and protocol != "h3":
        raise ValueError("tree-native-parser-ipc-rendezvous-overlap-css requires h3")
    if arm == "tree-native-parser-root-rendezvous-overlap-css" and protocol != "h3":
        raise ValueError("tree-native-parser-root-rendezvous-overlap-css requires h3")
    if arm == "tree-native-parser-process-overlap-css" and protocol != "h3":
        raise ValueError("tree-native-parser-process-overlap-css requires h3")
    if arm == "tree-native-parser-full-process-overlap-css" and protocol != "h3":
        raise ValueError("tree-native-parser-full-process-overlap-css requires h3")
    requested_arm = arm
    arm = {
        "document-first-buffer-http-connect": "document-first-buffer-overlap",
        "document-first-buffer-http-connect-delayed-padding": (
            "document-first-buffer-overlap"
        ),
        "document-first-buffer-http-connect-optimistic": (
            "document-first-buffer-overlap"
        ),
        "document-first-buffer-task-http-connect": (
            "document-first-buffer-task-overlap"
        ),
        "document-first-buffer-task-delayed-padding": (
            "document-first-buffer-task-overlap"
        ),
        "document-first-buffer-task-optimistic": ("document-first-buffer-task-overlap"),
        "document-headers-task-http-connect": "document-headers-task-overlap",
        "document-overlap-http-connect": "document-overlap",
        "document-start-http-connect": "document-start-overlap",
        "document-start-task-http-connect": "document-start-task-overlap",
        "tree-native-parser-resource-committed-page-http-connect": (
            "tree-native-parser-resource-committed-page"
        ),
    }.get(arm, arm)
    optimistic_lines = [
        line for line in log_lines if "Local optimistic reply phase=" in line
    ]
    parsed_optimistic = [
        OPTIMISTIC_LOCAL_REPLY.fullmatch(line) for line in optimistic_lines
    ]
    if any(marker is None for marker in parsed_optimistic):
        raise ValueError("malformed optimistic local reply evidence")
    optimistic_arms = {
        "document-first-buffer-task-optimistic": "socks",
        "document-first-buffer-http-connect-optimistic": "http-connect",
    }
    if requested_arm in optimistic_arms:
        expected_phases = (
            "queued",
            "reply-flushed-before-outer",
            "outer-established",
            "pump-started",
        )
        if tuple(marker["phase"] for marker in parsed_optimistic) != expected_phases:
            raise ValueError(
                "optimistic local reply lifecycle is incomplete or unordered"
            )
        if any(
            marker["listener"] != optimistic_arms[requested_arm]
            for marker in parsed_optimistic
        ):
            raise ValueError("optimistic local reply listener identity differs")
    elif parsed_optimistic:
        raise ValueError(f"{requested_arm} arm unexpectedly logged optimistic reply")
    result_lines = [line for line in log_lines if " preamble result=" in line]
    parsed_results = [PREAMBLE_RESULT.fullmatch(line) for line in result_lines]
    if any(result is None for result in parsed_results):
        raise ValueError("malformed preamble result evidence")
    preamble_arms = (
        "root",
        "root-pmtud-control",
        "document-complete",
        "document-carrier-dispatch",
        "document-cold-winner-handoff",
        "document-native-cache-open",
        "document-native-channel-open",
        "document-handshake-confirmed",
        "document-first-buffer-overlap",
        "document-first-buffer-task-overlap",
        "document-first-buffer-http-connect",
        "document-overlap",
        "document-headers-task-overlap",
        "document-overlap-http-connect",
        "document-start-http-connect",
        "document-start-overlap",
        "document-start-task-overlap",
        "tree-complete",
        "tree-complete-css",
        "tree-complete-resource-tree",
        "tree-early-overlap",
        "tree-early-overlap-resource-tree",
        "tree-root-overlap",
        "tree-root-overlap-css",
        "tree-resource-committed-overlap-css",
        "tree-resource-committed-overlap-tree",
        "tree-resource-committed-overlap-page",
        "tree-resource-native-cache-committed-overlap",
        "tree-native-parser-preload-overlap-css",
        "tree-native-parser-document-start-overlap-css",
        "tree-native-parser-document-start-resource-tree",
        "tree-native-parser-resource-committed-tree",
        "tree-native-parser-resource-committed-page",
        "tree-native-parser-document-start-navigation-stop-css",
        "tree-native-parser-document-start-response-stop-css",
        "tree-native-parser-document-handoff-overlap-css",
        "tree-native-parser-retarget-overlap-css",
        "tree-native-parser-ipc-rendezvous-overlap-css",
        "tree-native-parser-root-rendezvous-overlap-css",
        "tree-native-parser-process-overlap-css",
        "tree-native-parser-full-process-overlap-css",
        "tree-warm-css-304",
        "tree-overlap",
    )
    overlapping_arms = (
        "document-first-buffer-overlap",
        "document-first-buffer-task-overlap",
        "document-first-buffer-http-connect",
        "document-overlap",
        "document-headers-task-overlap",
        "document-overlap-http-connect",
        "document-start-http-connect",
        "document-start-overlap",
        "document-start-task-overlap",
        "tree-early-overlap",
        "tree-early-overlap-resource-tree",
        "tree-root-overlap",
        "tree-root-overlap-css",
        "tree-resource-committed-overlap-css",
        "tree-resource-committed-overlap-tree",
        "tree-resource-committed-overlap-page",
        "tree-resource-native-cache-committed-overlap",
        "tree-native-parser-preload-overlap-css",
        "tree-native-parser-document-start-overlap-css",
        "tree-native-parser-document-start-resource-tree",
        "tree-native-parser-resource-committed-tree",
        "tree-native-parser-resource-committed-page",
        "tree-native-parser-document-start-navigation-stop-css",
        "tree-native-parser-document-start-response-stop-css",
        "tree-native-parser-document-handoff-overlap-css",
        "tree-native-parser-retarget-overlap-css",
        "tree-native-parser-ipc-rendezvous-overlap-css",
        "tree-native-parser-root-rendezvous-overlap-css",
        "tree-native-parser-process-overlap-css",
        "tree-native-parser-full-process-overlap-css",
        "tree-warm-css-304",
        "tree-overlap",
    )
    if arm in preamble_arms:
        if len(parsed_results) != 1:
            raise ValueError(f"{arm} arm requires exactly one preamble result")
        result = parsed_results[0]
        if result["result"] != "success" or result["protocol"] != protocol:
            raise ValueError(f"{arm} arm preamble did not succeed on selected protocol")
        if not 200 <= int(result["http"]) < 300:
            raise ValueError(f"{arm} arm preamble success has invalid HTTP status")
    elif parsed_results:
        raise ValueError(f"{arm} arm unexpectedly ran a preamble")

    if arm in overlapping_arms and any(
        " preamble background drain timed out" in line for line in log_lines
    ):
        raise ValueError(f"{arm} arm preamble background drain timed out")

    document_admission_lines = [
        line for line in log_lines if " preamble document-overlap admission=" in line
    ]
    parsed_document_admissions = [
        DOCUMENT_OVERLAP_ADMISSION.fullmatch(line) for line in document_admission_lines
    ]
    if any(admission is None for admission in parsed_document_admissions):
        raise ValueError("malformed document-overlap admission evidence")
    document_drain_lines = [
        line for line in log_lines if " preamble document-overlap drain=" in line
    ]
    parsed_document_drains = [
        DOCUMENT_OVERLAP_DRAIN.fullmatch(line) for line in document_drain_lines
    ]
    if any(drain is None for drain in parsed_document_drains):
        raise ValueError("malformed document-overlap drain evidence")

    document_start_admission_lines = [
        line
        for line in log_lines
        if " preamble document-start-overlap admission=" in line
    ]
    parsed_document_start_admissions = [
        DOCUMENT_START_OVERLAP_ADMISSION.fullmatch(line)
        for line in document_start_admission_lines
    ]
    if any(admission is None for admission in parsed_document_start_admissions):
        raise ValueError("malformed document-start-overlap admission evidence")
    document_start_drain_lines = [
        line for line in log_lines if " preamble document-start-overlap drain=" in line
    ]
    parsed_document_start_drains = [
        DOCUMENT_START_OVERLAP_DRAIN.fullmatch(line)
        for line in document_start_drain_lines
    ]
    if any(drain is None for drain in parsed_document_start_drains):
        raise ValueError("malformed document-start-overlap drain evidence")

    native_resource_tree_admission_lines = [
        line
        for line in log_lines
        if " preamble native-parser-resource-tree admission=" in line
    ]
    parsed_native_resource_tree_admissions = [
        NATIVE_PARSER_RESOURCE_TREE_ADMISSION.fullmatch(line)
        for line in native_resource_tree_admission_lines
    ]
    native_resource_tree_open_lines = [
        line
        for line in log_lines
        if (
            "Preamble native-parser-resource-tree lifecycle=resource-opened " in line
            or "Preamble native-parser-resource-tree lifecycle=resource-prepared "
            in line
        )
    ]
    parsed_native_resource_tree_opens = [
        NATIVE_PARSER_RESOURCE_TREE_OPEN.fullmatch(line)
        for line in native_resource_tree_open_lines
    ]
    native_resource_tree_deferred_open_lines = [
        line
        for line in log_lines
        if "Preamble native-parser-resource-tree "
        "lifecycle=deferred-resource-opened " in line
    ]
    parsed_native_resource_tree_deferred_opens = [
        NATIVE_PARSER_RESOURCE_TREE_DEFERRED_OPEN.fullmatch(line)
        for line in native_resource_tree_deferred_open_lines
    ]
    native_resource_tree_commit_lines = [
        line
        for line in log_lines
        if "Preamble native-parser-resource-tree lifecycle=resource-committed " in line
    ]
    parsed_native_resource_tree_commits = [
        NATIVE_PARSER_RESOURCE_TREE_COMMIT.fullmatch(line)
        for line in native_resource_tree_commit_lines
    ]
    native_resource_tree_drain_lines = [
        line
        for line in log_lines
        if " preamble native-parser-resource-tree drain=" in line
    ]
    parsed_native_resource_tree_drains = [
        NATIVE_PARSER_RESOURCE_TREE_DRAIN.fullmatch(line)
        for line in native_resource_tree_drain_lines
    ]
    if any(
        marker is None
        for markers in (
            parsed_native_resource_tree_admissions,
            parsed_native_resource_tree_opens,
            parsed_native_resource_tree_deferred_opens,
            parsed_native_resource_tree_commits,
            parsed_native_resource_tree_drains,
        )
        for marker in markers
    ):
        raise ValueError("malformed native parser resource-tree evidence")

    admission_lines = [
        line for line in log_lines if " preamble root-overlap admission=" in line
    ]
    parsed_admissions = [
        ROOT_OVERLAP_ADMISSION.fullmatch(line) for line in admission_lines
    ]
    if any(admission is None for admission in parsed_admissions):
        raise ValueError("malformed tree-root-overlap admission evidence")
    drain_lines = [
        line for line in log_lines if " preamble root-overlap drain=" in line
    ]
    parsed_drains = [ROOT_OVERLAP_DRAIN.fullmatch(line) for line in drain_lines]
    if any(drain is None for drain in parsed_drains):
        raise ValueError("malformed tree-root-overlap drain evidence")
    resource_commit_admission_lines = [
        line
        for line in log_lines
        if " preamble resource-committed-overlap admission=" in line
    ]
    parsed_resource_commit_admissions = [
        RESOURCE_COMMITTED_ADMISSION.fullmatch(line)
        for line in resource_commit_admission_lines
    ]
    if any(marker is None for marker in parsed_resource_commit_admissions):
        raise ValueError("malformed resource-committed admission evidence")
    resource_commit_drain_lines = [
        line
        for line in log_lines
        if " preamble resource-committed-overlap drain=" in line
    ]
    parsed_resource_commit_drains = [
        RESOURCE_COMMITTED_DRAIN.fullmatch(line) for line in resource_commit_drain_lines
    ]
    if any(marker is None for marker in parsed_resource_commit_drains):
        raise ValueError("malformed resource-committed drain evidence")
    resource_commit_task_barrier_lines = [
        line
        for line in log_lines
        if "Preamble resource-committed-overlap barrier=task-dispatched " in line
    ]
    resource_native_cache_admission_lines = [
        line
        for line in log_lines
        if " preamble resource-native-cache-committed-overlap admission=" in line
    ]
    parsed_resource_native_cache_admissions = [
        RESOURCE_NATIVE_CACHE_COMMITTED_ADMISSION.fullmatch(line)
        for line in resource_native_cache_admission_lines
    ]
    if any(marker is None for marker in parsed_resource_native_cache_admissions):
        raise ValueError("malformed native resource cache admission evidence")
    resource_native_cache_drain_lines = [
        line
        for line in log_lines
        if " preamble resource-native-cache-committed-overlap drain=" in line
    ]
    parsed_resource_native_cache_drains = [
        RESOURCE_NATIVE_CACHE_COMMITTED_DRAIN.fullmatch(line)
        for line in resource_native_cache_drain_lines
    ]
    if any(marker is None for marker in parsed_resource_native_cache_drains):
        raise ValueError("malformed native resource cache drain evidence")
    native_parser_discovery_lines = [
        line for line in log_lines if " preamble native-parser-preload parser=" in line
    ]
    parsed_native_parser_discoveries = [
        NATIVE_PARSER_PRELOAD_DISCOVERY.fullmatch(line)
        for line in native_parser_discovery_lines
    ]
    if any(marker is None for marker in parsed_native_parser_discoveries):
        raise ValueError("malformed native parser preload discovery evidence")
    native_parser_descriptor_lines = [
        line
        for line in log_lines
        if "Preamble native-parser-preload lifecycle=chunk-flushed " in line
        and " descriptors=1 " in line
        and " status=0x00000000 " in line
        and line.endswith(f" protocol={protocol}")
    ]
    native_resource_tree_descriptor_count = (
        7 if arm == "tree-native-parser-resource-committed-page" else 4
    )
    native_resource_tree_descriptor_lines = [
        line
        for line in log_lines
        if "Preamble native-parser-preload lifecycle=chunk-flushed " in line
        and f" descriptors={native_resource_tree_descriptor_count} " in line
        and " status=0x00000000 " in line
        and line.endswith(f" protocol={protocol}")
    ]
    native_resource_tree_body_barrier_lines = [
        line
        for line in log_lines
        if "Preamble native-parser-resource-tree "
        "barrier=first-resource-body-buffer " in line
    ]
    native_resource_tree_first_body_lines = [
        line
        for line in log_lines
        if "Preamble native-parser-resource-tree "
        "lifecycle=first-resource-body-buffer-consumed " in line
    ]
    parsed_native_resource_tree_first_bodies = [
        re.fullmatch(
            r"(?:\[[^\]\r\n]+\] )?Preamble native-parser-resource-tree "
            r"lifecycle=first-resource-body-buffer-consumed "
            r"stream=(?P<stream>[1-6]) protocol=(?P<protocol>h2|h3)",
            line,
        )
        for line in native_resource_tree_first_body_lines
    ]
    if any(marker is None for marker in parsed_native_resource_tree_first_bodies):
        raise ValueError("malformed native resource-tree first-body evidence")
    native_parser_lightweight_open_lines = [
        line
        for line in log_lines
        if "Preamble native-parser-preload "
        "lifecycle=stylesheet-opened stream=1 kind=from-parser "
        f"referrer=inherited protocol={protocol}" in line
    ]
    wrong_protocol_native_parser_lines = [
        line
        for line in log_lines
        if "Preamble native-parser-preload " in line
        and " protocol=" in line
        and not line.endswith(f" protocol={protocol}")
    ]
    if wrong_protocol_native_parser_lines:
        raise ValueError("native parser lifecycle logged the wrong outer protocol")
    native_parser_channel_lines = [
        line for line in log_lines if " preamble native-parser-preload channel=" in line
    ]
    parsed_native_parser_channels = [
        NATIVE_PARSER_PRELOAD_CHANNEL.fullmatch(line)
        for line in native_parser_channel_lines
    ]
    if any(marker is None for marker in parsed_native_parser_channels):
        raise ValueError("malformed native parser preload channel evidence")
    native_parser_admission_lines = [
        line
        for line in log_lines
        if " preamble native-parser-preload admission=" in line
    ]
    parsed_native_parser_admissions = [
        NATIVE_PARSER_PRELOAD_ADMISSION.fullmatch(line)
        for line in native_parser_admission_lines
    ]
    if any(marker is None for marker in parsed_native_parser_admissions):
        raise ValueError("malformed native parser preload admission evidence")
    native_parser_barrier_lines = [
        line for line in log_lines if " preamble native-parser-preload barrier=" in line
    ]
    parsed_native_parser_barriers = [
        NATIVE_PARSER_PRELOAD_BARRIER.fullmatch(line)
        for line in native_parser_barrier_lines
    ]
    if any(marker is None for marker in parsed_native_parser_barriers):
        raise ValueError("malformed native parser preload barrier evidence")
    native_parser_drain_lines = [
        line for line in log_lines if " preamble native-parser-preload drain=" in line
    ]
    parsed_native_parser_drains = [
        NATIVE_PARSER_PRELOAD_DRAIN.fullmatch(line)
        for line in native_parser_drain_lines
    ]
    if any(marker is None for marker in parsed_native_parser_drains):
        raise ValueError("malformed native parser preload drain evidence")
    native_parser_document_start_admission_lines = [
        line
        for line in log_lines
        if " preamble native-parser-document-start admission=" in line
    ]
    parsed_native_parser_document_start_admissions = [
        NATIVE_PARSER_DOCUMENT_START_ADMISSION.fullmatch(line)
        for line in native_parser_document_start_admission_lines
    ]
    if any(marker is None for marker in parsed_native_parser_document_start_admissions):
        raise ValueError("malformed native parser document-start admission evidence")
    native_parser_navigation_stop_evidence_lines = [
        line
        for line in log_lines
        if " preamble native-parser-document-start-navigation-stop " in line
    ]
    native_parser_navigation_stop_admission_lines = [
        line
        for line in native_parser_navigation_stop_evidence_lines
        if " admission=" in line
    ]
    native_parser_navigation_stop_stylesheet_lines = [
        line
        for line in native_parser_navigation_stop_evidence_lines
        if " phase=stylesheet-committed " in line
    ]
    native_parser_navigation_stop_issued_lines = [
        line
        for line in native_parser_navigation_stop_evidence_lines
        if " phase=navigation-stop-issued " in line
    ]
    native_parser_navigation_stop_tunnel_active_lines = [
        line
        for line in native_parser_navigation_stop_evidence_lines
        if " phase=tunnel-application-active " in line
    ]
    native_parser_navigation_stop_response_started_lines = [
        line
        for line in native_parser_navigation_stop_evidence_lines
        if " phase=stylesheet-response-started " in line
    ]
    native_parser_navigation_stop_onstop_lines = [
        line
        for line in native_parser_navigation_stop_evidence_lines
        if " phase=stylesheet-onstop " in line
    ]
    native_parser_navigation_stop_drain_lines = [
        line
        for line in native_parser_navigation_stop_evidence_lines
        if " drain=complete " in line
    ]
    native_parser_navigation_stop_groups = (
        (
            native_parser_navigation_stop_admission_lines,
            NATIVE_PARSER_NAVIGATION_STOP_ADMISSION,
        ),
        (
            native_parser_navigation_stop_stylesheet_lines,
            NATIVE_PARSER_NAVIGATION_STOP_STYLESHEET_COMMITTED,
        ),
        (
            native_parser_navigation_stop_tunnel_active_lines,
            NATIVE_PARSER_NAVIGATION_STOP_TUNNEL_ACTIVE,
        ),
        (
            native_parser_navigation_stop_response_started_lines,
            NATIVE_PARSER_NAVIGATION_STOP_RESPONSE_STARTED,
        ),
        (
            native_parser_navigation_stop_issued_lines,
            NATIVE_PARSER_NAVIGATION_STOP_ISSUED,
        ),
        (
            native_parser_navigation_stop_onstop_lines,
            NATIVE_PARSER_NAVIGATION_STOP_ONSTOP,
        ),
        (
            native_parser_navigation_stop_drain_lines,
            NATIVE_PARSER_NAVIGATION_STOP_DRAIN,
        ),
    )
    parsed_native_parser_navigation_stop_groups = tuple(
        [pattern.fullmatch(line) for line in lines]
        for lines, pattern in native_parser_navigation_stop_groups
    )
    if sum(len(lines) for lines, _ in native_parser_navigation_stop_groups) != len(
        native_parser_navigation_stop_evidence_lines
    ) or any(
        marker is None
        for markers in parsed_native_parser_navigation_stop_groups
        for marker in markers
    ):
        raise ValueError("malformed or unknown native parser navigation-stop evidence")
    native_parser_response_stop_evidence_lines = [
        line
        for line in log_lines
        if " preamble native-parser-document-start-response-stop " in line
    ]
    native_parser_response_stop_admission_lines = [
        line
        for line in native_parser_response_stop_evidence_lines
        if " admission=" in line
    ]
    native_parser_response_stop_stylesheet_lines = [
        line
        for line in native_parser_response_stop_evidence_lines
        if " phase=stylesheet-committed " in line
    ]
    native_parser_response_stop_tunnel_active_lines = [
        line
        for line in native_parser_response_stop_evidence_lines
        if " phase=tunnel-application-active " in line
    ]
    native_parser_response_stop_response_started_lines = [
        line
        for line in native_parser_response_stop_evidence_lines
        if " phase=stylesheet-response-started " in line
    ]
    native_parser_response_stop_issued_lines = [
        line
        for line in native_parser_response_stop_evidence_lines
        if " phase=navigation-stop-issued " in line
    ]
    native_parser_response_stop_onstop_lines = [
        line
        for line in native_parser_response_stop_evidence_lines
        if " phase=stylesheet-onstop " in line
    ]
    native_parser_response_stop_drain_lines = [
        line
        for line in native_parser_response_stop_evidence_lines
        if " drain=complete " in line
    ]
    native_parser_response_stop_groups = (
        (
            native_parser_response_stop_admission_lines,
            NATIVE_PARSER_RESPONSE_STOP_ADMISSION,
        ),
        (
            native_parser_response_stop_stylesheet_lines,
            NATIVE_PARSER_RESPONSE_STOP_STYLESHEET_COMMITTED,
        ),
        (
            native_parser_response_stop_tunnel_active_lines,
            NATIVE_PARSER_RESPONSE_STOP_TUNNEL_ACTIVE,
        ),
        (
            native_parser_response_stop_response_started_lines,
            NATIVE_PARSER_RESPONSE_STOP_RESPONSE_STARTED,
        ),
        (
            native_parser_response_stop_issued_lines,
            NATIVE_PARSER_RESPONSE_STOP_ISSUED,
        ),
        (
            native_parser_response_stop_onstop_lines,
            NATIVE_PARSER_RESPONSE_STOP_ONSTOP,
        ),
        (
            native_parser_response_stop_drain_lines,
            NATIVE_PARSER_RESPONSE_STOP_DRAIN,
        ),
    )
    parsed_native_parser_response_stop_groups = tuple(
        [pattern.fullmatch(line) for line in lines]
        for lines, pattern in native_parser_response_stop_groups
    )
    if sum(len(lines) for lines, _ in native_parser_response_stop_groups) != len(
        native_parser_response_stop_evidence_lines
    ) or any(
        marker is None
        for markers in parsed_native_parser_response_stop_groups
        for marker in markers
    ):
        raise ValueError("malformed or unknown native parser response-stop evidence")
    native_parser_document_handoff_lines = [
        line
        for line in log_lines
        if " preamble native-parser-document-handoff phase=" in line
    ]
    parsed_native_parser_document_handoffs = [
        NATIVE_PARSER_DOCUMENT_HANDOFF.fullmatch(line)
        for line in native_parser_document_handoff_lines
    ]
    if any(marker is None for marker in parsed_native_parser_document_handoffs):
        raise ValueError("malformed native parser document handoff evidence")
    native_parser_retarget_evidence_lines = [
        line for line in log_lines if " preamble native-parser-retarget " in line
    ]
    native_parser_retarget_lines = [
        line
        for line in native_parser_retarget_evidence_lines
        if " preamble native-parser-retarget phase=" in line
    ]
    parsed_native_parser_retargets = [
        NATIVE_PARSER_RETARGET.fullmatch(line) for line in native_parser_retarget_lines
    ]
    if any(marker is None for marker in parsed_native_parser_retargets):
        raise ValueError("malformed native parser retarget evidence")
    if len(native_parser_retarget_evidence_lines) != len(native_parser_retarget_lines):
        raise ValueError(
            "native parser retarget emitted fallback, failure, or unknown evidence"
        )
    native_parser_root_replacement_evidence_lines = [
        line
        for line in log_lines
        if " preamble native-parser-root-replacement phase=" in line
    ]
    parsed_native_parser_root_replacements = [
        NATIVE_PARSER_ROOT_REPLACEMENT.fullmatch(line)
        for line in native_parser_root_replacement_evidence_lines
    ]
    if any(marker is None for marker in parsed_native_parser_root_replacements):
        raise ValueError(
            "native parser root replacement emitted fallback or unknown evidence"
        )
    native_style_activation_evidence_lines = [
        line
        for line in log_lines
        if "Native style activation phase=" in line and " request=" in line
    ]
    native_style_channel_created_lines = [
        line for line in log_lines if NATIVE_STYLE_CHANNEL_CREATED.fullmatch(line)
    ]
    native_style_opened_lines = [
        line for line in log_lines if NATIVE_STYLE_OPENED.fullmatch(line)
    ]
    native_style_activation_lines = [
        line
        for line in native_style_activation_evidence_lines
        if NATIVE_STYLE_ACTIVATION.fullmatch(line)
    ]
    parsed_native_style_activations = [
        NATIVE_STYLE_ACTIVATION.fullmatch(line)
        for line in native_style_activation_lines
    ]
    if len(native_style_activation_evidence_lines) != len(
        native_style_activation_lines
    ):
        raise ValueError(
            "native style activation emitted failure, cancellation, or unknown "
            "request evidence"
        )
    native_root_activation_evidence_lines = [
        line
        for line in log_lines
        if "Native root replacement activation phase=" in line
    ]
    parsed_native_root_activations = [
        NATIVE_ROOT_PHASE.fullmatch(line)
        for line in native_root_activation_evidence_lines
    ]
    if any(marker is None for marker in parsed_native_root_activations):
        raise ValueError(
            "native root replacement emitted malformed or unknown evidence"
        )
    established_lines = [line for line in log_lines if " established target=" in line]
    parsed_established = [ESTABLISHED.fullmatch(line) for line in established_lines]
    if any(established is None for established in parsed_established):
        raise ValueError("malformed CONNECT-established evidence")
    expected_padding = os.environ.get("NAIVEFOX_CAPTURE_EXPECT_PADDING", "yes")
    if expected_padding not in ("yes", "no"):
        raise ValueError("unsupported expected padding condition")
    if any(
        established["padding"] != expected_padding for established in parsed_established
    ):
        raise ValueError("CONNECT-established padding condition differs from expected")
    delayed_padding_lines = [
        line for line in log_lines if " diagnostic-delayed-padding-phase " in line
    ]
    parsed_delayed_padding = [
        DELAYED_PADDING_PHASE.fullmatch(line) for line in delayed_padding_lines
    ]
    if any(marker is None for marker in parsed_delayed_padding):
        raise ValueError("malformed delayed padding phase evidence")
    delayed_padding_arms = {
        "document-first-buffer-task-delayed-padding",
        "document-first-buffer-http-connect-delayed-padding",
    }
    if requested_arm in delayed_padding_arms:
        if len(parsed_delayed_padding) != 1:
            raise ValueError("delayed padding arm requires one negotiation marker")
        marker = parsed_delayed_padding[0]
        matching_established = [
            line
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == marker["connection"]
            and established["protocol"] == marker["protocol"] == protocol
        ]
        if len(matching_established) != 1 or not (
            log_lines.index(matching_established[0])
            < log_lines.index(delayed_padding_lines[0])
        ):
            raise ValueError("delayed padding negotiation identity or order differs")
    elif parsed_delayed_padding:
        raise ValueError(f"{requested_arm} arm unexpectedly used delayed padding")
    native_cache_lines = [
        line for line in log_lines if " preamble native-cache-open cache=" in line
    ]
    parsed_native_cache = [
        NATIVE_CACHE_OPEN.fullmatch(line) for line in native_cache_lines
    ]
    if any(marker is None for marker in parsed_native_cache):
        raise ValueError("malformed native cache-open evidence")
    if arm == "document-native-cache-open":
        if len(parsed_native_cache) != 1:
            raise ValueError(
                "document-native-cache-open requires one read-only miss marker"
            )
        marker = parsed_native_cache[0]
        matching_established = [
            (line, established)
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == marker["connection"]
            and established["protocol"] == protocol
        ]
        if len(matching_established) != 1 or marker["protocol"] != protocol:
            raise ValueError(
                "document-native-cache-open marker identity differs from CONNECT"
            )
        if not (
            log_lines.index(native_cache_lines[0])
            < log_lines.index(result_lines[0])
            < log_lines.index(matching_established[0][0])
        ):
            raise ValueError(
                "native cache-open lifecycle markers have invalid ordering"
            )
    elif parsed_native_cache:
        raise ValueError(f"{arm} arm unexpectedly logged native cache-open lifecycle")
    native_channel_lines = [
        line for line in log_lines if " preamble native-channel-open " in line
    ]
    parsed_native_channel = [
        NATIVE_CHANNEL_OPEN.fullmatch(line) for line in native_channel_lines
    ]
    if any(marker is None for marker in parsed_native_channel):
        raise ValueError("malformed native channel-open evidence")
    if arm == "document-native-channel-open":
        if len(parsed_native_channel) != 1:
            raise ValueError(
                "document-native-channel-open requires one strict success marker"
            )
        marker = parsed_native_channel[0]
        matching_established = [
            (line, established)
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == marker["connection"]
            and established["protocol"] == protocol
        ]
        if len(matching_established) != 1 or marker["protocol"] != protocol:
            raise ValueError(
                "document-native-channel-open marker identity differs from CONNECT"
            )
        if not (
            log_lines.index(native_channel_lines[0])
            < log_lines.index(result_lines[0])
            < log_lines.index(matching_established[0][0])
        ):
            raise ValueError(
                "native channel-open lifecycle markers have invalid ordering"
            )
    elif parsed_native_channel:
        raise ValueError(f"{arm} arm unexpectedly logged native channel lifecycle")
    cold_winner_lines = [
        line for line in log_lines if " preamble cold-winner-handoff " in line
    ]
    parsed_cold_winner = [
        COLD_WINNER_HANDOFF.fullmatch(line) for line in cold_winner_lines
    ]
    if any(marker is None for marker in parsed_cold_winner):
        raise ValueError("malformed cold winner-handoff evidence")
    if arm == "document-cold-winner-handoff":
        if len(parsed_cold_winner) != 1:
            raise ValueError(
                "document-cold-winner-handoff requires one exact winner marker"
            )
        marker = parsed_cold_winner[0]
        matching_established = [
            (line, established)
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == marker["connection"]
            and established["protocol"] == protocol
        ]
        if len(matching_established) != 1 or marker["protocol"] != protocol:
            raise ValueError("cold winner-handoff marker identity differs from CONNECT")
        if not (
            log_lines.index(cold_winner_lines[0])
            < log_lines.index(result_lines[0])
            < log_lines.index(matching_established[0][0])
        ):
            raise ValueError("cold winner-handoff markers have invalid ordering")
    elif parsed_cold_winner:
        raise ValueError(f"{arm} arm unexpectedly logged cold winner lifecycle")
    if arm in (
        "document-overlap",
        "document-headers-task-overlap",
        "document-overlap-http-connect",
        "document-first-buffer-overlap",
        "document-first-buffer-task-overlap",
        "document-first-buffer-http-connect",
    ):
        if len(parsed_document_admissions) != 1:
            raise ValueError(
                "document-overlap requires exactly one causal admission marker"
            )
        admission = parsed_document_admissions[0]
        if (
            admission["admission"]
            != (
                "response-headers-task"
                if arm == "document-headers-task-overlap"
                else "first-data-buffer-task"
                if arm == "document-first-buffer-task-overlap"
                else "first-data-buffer"
                if arm
                in (
                    "document-first-buffer-overlap",
                    "document-first-buffer-http-connect",
                )
                else "response-headers"
            )
            or admission["response_accepted"] != "1"
            or admission["root_done"] != "0"
            or admission["protocol"] != protocol
        ):
            raise ValueError("document-overlap causal admission state is invalid")
        if len(parsed_document_drains) != 1:
            raise ValueError(
                "document-overlap requires exactly one completed drain marker"
            )
        drain = parsed_document_drains[0]
        matching_established = [
            (line, established)
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == admission["connection"]
            and established["protocol"] == protocol
        ]
        if len(matching_established) != 1:
            raise ValueError(
                "document-overlap requires exactly one matching "
                "CONNECT-established marker"
            )
        established_line, _ = matching_established[0]
        if (
            result["connection"] != admission["connection"]
            or drain["connection"] != admission["connection"]
            or drain["protocol"] != protocol
            or int(drain["completed_resources"]) != 0
        ):
            raise ValueError("document-overlap lifecycle marker identity differs")
        admission_index = log_lines.index(document_admission_lines[0])
        result_index = log_lines.index(result_lines[0])
        drain_index = log_lines.index(document_drain_lines[0])
        established_index = log_lines.index(established_line)
        valid_order = admission_index < result_index < drain_index
        if arm in (
            "document-first-buffer-overlap",
            "document-first-buffer-task-overlap",
            "document-first-buffer-task-delayed-padding",
            "document-first-buffer-http-connect",
            "document-first-buffer-http-connect-delayed-padding",
        ):
            valid_order = valid_order and admission_index < established_index
        else:
            valid_order = valid_order and result_index < established_index
        if not valid_order:
            raise ValueError("document-overlap lifecycle markers have invalid ordering")
    elif parsed_document_admissions or parsed_document_drains:
        raise ValueError(f"{arm} arm unexpectedly logged document-overlap lifecycle")
    if arm in (
        "document-start-http-connect",
        "document-start-overlap",
        "document-start-task-overlap",
    ):
        if len(parsed_document_start_admissions) != 1:
            raise ValueError(
                "document-start-overlap requires exactly one causal admission marker"
            )
        admission = parsed_document_start_admissions[0]
        if (
            admission["admission"]
            != (
                "request-committed-task"
                if arm == "document-start-task-overlap"
                else "request-committed"
            )
            or admission["request_committed"] != "1"
            or admission["root_done"] != "0"
            or admission["protocol"] != protocol
        ):
            raise ValueError("document-start-overlap causal admission state is invalid")
        if len(parsed_document_start_drains) != 1:
            raise ValueError(
                "document-start-overlap requires exactly one completed drain marker"
            )
        drain = parsed_document_start_drains[0]
        matching_established = [
            (line, established)
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == admission["connection"]
            and established["protocol"] == protocol
        ]
        if len(matching_established) != 1:
            raise ValueError(
                "document-start-overlap requires exactly one matching "
                "CONNECT-established marker"
            )
        established_line, _ = matching_established[0]
        if (
            result["connection"] != admission["connection"]
            or drain["connection"] != admission["connection"]
            or drain["protocol"] != protocol
            or int(drain["completed_resources"]) != 0
        ):
            raise ValueError("document-start-overlap lifecycle marker identity differs")
        admission_index = log_lines.index(document_start_admission_lines[0])
        result_index = log_lines.index(result_lines[0])
        drain_index = log_lines.index(document_start_drain_lines[0])
        established_index = log_lines.index(established_line)
        if not (
            admission_index < result_index < drain_index
            and admission_index < established_index
        ):
            raise ValueError(
                "document-start-overlap lifecycle markers have invalid ordering"
            )
    elif parsed_document_start_admissions or parsed_document_start_drains:
        raise ValueError(
            f"{arm} arm unexpectedly logged document-start-overlap lifecycle"
        )
    if arm in (
        "tree-root-overlap",
        "tree-root-overlap-css",
        "tree-warm-css-304",
    ):
        expected_resources = (
            1 if arm in ("tree-root-overlap-css", "tree-warm-css-304") else 2
        )
        expected_task_barriers = (
            1 if arm == "tree-resource-committed-overlap-page" else 0
        )
        if len(resource_commit_task_barrier_lines) != expected_task_barriers:
            raise ValueError("resource-committed arm has invalid task-barrier evidence")
        if (
            resource_commit_task_barrier_lines
            and not resource_commit_task_barrier_lines[0].endswith(
                "Preamble resource-committed-overlap barrier=task-dispatched "
                f"assets=6 protocol={protocol}"
            )
        ):
            raise ValueError("resource-committed task-barrier identity is invalid")
        if len(parsed_admissions) != 1:
            raise ValueError(
                "tree-root-overlap requires exactly one causal admission marker"
            )
        admission = parsed_admissions[0]
        if (
            admission["admission"] != "started-resources"
            or admission["root_done"] != "1"
            or int(admission["started_resources"]) != expected_resources
            or admission["protocol"] != protocol
        ):
            raise ValueError("tree-root-overlap causal admission state is invalid")
        if len(parsed_drains) != 1:
            raise ValueError(
                "tree-root-overlap requires exactly one completed drain marker"
            )
        matching_established = [
            (line, established)
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == admission["connection"]
            and established["protocol"] == protocol
        ]
        if len(matching_established) != 1:
            raise ValueError(
                "tree-root-overlap requires exactly one matching "
                "CONNECT-established marker"
            )
        drain = parsed_drains[0]
        established_line, established = matching_established[0]
        if (
            result["connection"] != admission["connection"]
            or drain["connection"] != admission["connection"]
            or drain["protocol"] != protocol
        ):
            raise ValueError("tree-root-overlap lifecycle marker identity differs")
        if int(drain["completed_resources"]) != expected_resources:
            raise ValueError(
                "tree-root-overlap fixture resource completion count is invalid"
            )
        admission_index = log_lines.index(admission_lines[0])
        result_index = log_lines.index(result_lines[0])
        drain_index = log_lines.index(drain_lines[0])
        established_index = log_lines.index(established_line)
        if not (
            admission_index < result_index < drain_index
            and result_index < established_index
        ):
            raise ValueError(
                "tree-root-overlap lifecycle markers have invalid ordering"
            )
    elif parsed_admissions or parsed_drains:
        raise ValueError(f"{arm} arm unexpectedly logged root-overlap lifecycle")

    if arm in (
        "tree-resource-committed-overlap-css",
        "tree-resource-committed-overlap-tree",
        "tree-resource-committed-overlap-page",
    ):
        if len(parsed_resource_commit_admissions) != 1:
            raise ValueError(
                "resource-committed arm requires one causal admission marker"
            )
        if len(parsed_resource_commit_drains) != 1:
            raise ValueError("resource-committed arm requires one drain marker")
        admission = parsed_resource_commit_admissions[0]
        drain = parsed_resource_commit_drains[0]
        expected_resources = (
            6
            if arm == "tree-resource-committed-overlap-page"
            else 3
            if arm == "tree-resource-committed-overlap-tree"
            else 1
        )
        if (
            admission["admission"] != "request-committed"
            or admission["root_done"] != "1"
            or int(admission["started_resources"]) != expected_resources
            or int(admission["committed_resources"]) != expected_resources
            or admission["protocol"] != "h3"
            or int(drain["completed_resources"]) != expected_resources
            or drain["protocol"] != "h3"
            or result["connection"] != admission["connection"]
            or drain["connection"] != admission["connection"]
        ):
            raise ValueError("resource-committed causal state is invalid")
        matching_established = [
            line
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == admission["connection"]
            and established["protocol"] == "h3"
        ]
        if len(matching_established) != 1:
            raise ValueError(
                "resource-committed arm requires one matching CONNECT marker"
            )
        if not (
            log_lines.index(resource_commit_admission_lines[0])
            < log_lines.index(result_lines[0])
            < log_lines.index(resource_commit_drain_lines[0])
            and log_lines.index(result_lines[0])
            < log_lines.index(matching_established[0])
        ):
            raise ValueError("resource-committed markers have invalid ordering")
    elif parsed_resource_commit_admissions or parsed_resource_commit_drains:
        raise ValueError(f"{arm} arm unexpectedly logged resource-committed lifecycle")

    if arm == "tree-resource-native-cache-committed-overlap":
        if len(parsed_resource_native_cache_admissions) != 1:
            raise ValueError(
                "native resource cache arm requires one causal admission marker"
            )
        if len(parsed_resource_native_cache_drains) != 1:
            raise ValueError("native resource cache arm requires one drain marker")
        admission = parsed_resource_native_cache_admissions[0]
        drain = parsed_resource_native_cache_drains[0]
        if (
            admission["admission"] != "request-committed"
            or admission["root_done"] != "1"
            or admission["started_resources"] != "1"
            or admission["committed_resources"] != "1"
            or admission["cache_new"] != "1"
            or admission["protocol"] != "h3"
            or drain["completed_resources"] != "1"
            or drain["cache_new"] != "1"
            or drain["protocol"] != "h3"
            or result["connection"] != admission["connection"]
            or drain["connection"] != admission["connection"]
        ):
            raise ValueError("native resource cache causal state is invalid")
        matching_established = [
            line
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == admission["connection"]
            and established["protocol"] == "h3"
        ]
        if len(matching_established) != 1:
            raise ValueError(
                "native resource cache arm requires one matching CONNECT marker"
            )
        if not (
            log_lines.index(resource_native_cache_admission_lines[0])
            < log_lines.index(result_lines[0])
            < log_lines.index(resource_native_cache_drain_lines[0])
            and log_lines.index(result_lines[0])
            < log_lines.index(matching_established[0])
        ):
            raise ValueError("native resource cache markers have invalid ordering")
    elif parsed_resource_native_cache_admissions or parsed_resource_native_cache_drains:
        raise ValueError(
            f"{arm} arm unexpectedly logged native resource cache lifecycle"
        )

    native_parser_markers = (
        parsed_native_parser_discoveries,
        parsed_native_parser_channels,
        parsed_native_parser_admissions,
        parsed_native_parser_barriers,
        parsed_native_parser_drains,
    )
    native_parser_complete_before_connect_arms = (
        "tree-native-parser-preload-overlap-css",
        "tree-native-parser-document-handoff-overlap-css",
        "tree-native-parser-retarget-overlap-css",
        "tree-native-parser-ipc-rendezvous-overlap-css",
        "tree-native-parser-root-rendezvous-overlap-css",
        "tree-native-parser-process-overlap-css",
        "tree-native-parser-full-process-overlap-css",
    )
    if arm in native_parser_complete_before_connect_arms:
        if any(len(markers) != 1 for markers in native_parser_markers):
            raise ValueError(
                "native parser preload arm requires exactly one parser, "
                "descriptor, channel, admission, barrier, and drain marker"
            )
        discovery = parsed_native_parser_discoveries[0]
        channel = parsed_native_parser_channels[0]
        admission = parsed_native_parser_admissions[0]
        barrier = parsed_native_parser_barriers[0]
        drain = parsed_native_parser_drains[0]
        connection = discovery["connection"]
        if (
            discovery["parsers"] != "1"
            or discovery["descriptors"] != "1"
            or discovery["provenance"] != "FromParser"
            or discovery["internal_type"] != "40"
            or channel["channels"] != "1"
            or admission["admission"] != "request-committed"
            or admission["root_done"] != "1"
            or admission["started_resources"] != "1"
            or admission["committed_resources"] != "1"
            or drain["completed_resources"] != "1"
            or not 200 <= int(drain["http"]) < 300
            or any(
                marker["connection"] != connection or marker["protocol"] != protocol
                for marker in (channel, admission, barrier, drain)
            )
            or result["connection"] != connection
        ):
            raise ValueError("native parser preload causal state is invalid")
        matching_established = [
            line
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == connection
            and established["protocol"] == "h3"
        ]
        if len(matching_established) != 1:
            raise ValueError(
                "native parser preload arm requires one matching CONNECT marker"
            )
        indices = (
            log_lines.index(native_parser_discovery_lines[0]),
            log_lines.index(native_parser_channel_lines[0]),
            log_lines.index(native_parser_admission_lines[0]),
            log_lines.index(native_parser_barrier_lines[0]),
            log_lines.index(result_lines[0]),
            log_lines.index(matching_established[0]),
            log_lines.index(native_parser_drain_lines[0]),
        )
        if tuple(sorted(indices)) != indices or len(set(indices)) != len(indices):
            raise ValueError(
                "native parser preload lifecycle markers have invalid ordering"
            )
    elif any(native_parser_markers):
        if arm not in (
            "tree-native-parser-document-start-overlap-css",
            "tree-native-parser-document-start-navigation-stop-css",
            "tree-native-parser-document-start-response-stop-css",
        ):
            raise ValueError(
                f"{arm} arm unexpectedly logged native parser preload lifecycle"
            )

    if arm == "tree-native-parser-document-start-overlap-css":
        if len(parsed_native_parser_document_start_admissions) != 1:
            raise ValueError(
                "native parser document-start arm requires one early admission"
            )
        if (
            parsed_native_parser_discoveries
            or len(native_parser_descriptor_lines) != 1
            or parsed_native_parser_channels
            or len(native_parser_lightweight_open_lines) != 1
            or len(parsed_native_parser_drains) != 1
            or parsed_native_parser_admissions
            or parsed_native_parser_barriers
        ):
            raise ValueError(
                "native parser document-start arm requires one background parser, "
                "descriptor, channel, and drain without a late parser barrier"
            )
        early = parsed_native_parser_document_start_admissions[0]
        drain = parsed_native_parser_drains[0]
        connection = early["connection"]
        if (
            early["admission"] != "request-committed"
            or early["request_committed"] != "1"
            or early["root_done"] != "0"
            or drain["completed_resources"] != "1"
            or not 200 <= int(drain["http"]) < 300
            or drain["connection"] != connection
            or drain["protocol"] != protocol
            or early["protocol"] != protocol
            or result["connection"] != connection
        ):
            raise ValueError("native parser document-start causal state is invalid")
        matching_established = [
            line
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == connection
            and established["protocol"] == protocol
        ]
        if len(matching_established) != 1:
            raise ValueError(
                "native parser document-start arm requires one matching CONNECT marker"
            )
        early_index = log_lines.index(native_parser_document_start_admission_lines[0])
        discovery_index = log_lines.index(native_parser_descriptor_lines[0])
        channel_index = log_lines.index(native_parser_lightweight_open_lines[0])
        result_index = log_lines.index(result_lines[0])
        drain_index = log_lines.index(native_parser_drain_lines[0])
        established_index = log_lines.index(matching_established[0])
        if not (
            early_index < established_index
            and early_index < discovery_index < channel_index < result_index
            and result_index < drain_index
        ):
            raise ValueError(
                "native parser document-start lifecycle markers have invalid ordering"
            )
    elif parsed_native_parser_document_start_admissions:
        raise ValueError(
            f"{arm} arm unexpectedly logged native parser document-start admission"
        )

    native_resource_tree_markers = (
        parsed_native_resource_tree_admissions,
        parsed_native_resource_tree_opens,
        parsed_native_resource_tree_deferred_opens,
        parsed_native_resource_tree_commits,
        parsed_native_resource_tree_first_bodies,
        parsed_native_resource_tree_drains,
    )
    if arm in (
        "tree-native-parser-document-start-resource-tree",
        "tree-native-parser-resource-committed-tree",
        "tree-native-parser-resource-committed-page",
    ):
        expected_resource_count = (
            6 if arm == "tree-native-parser-resource-committed-page" else 3
        )
        expected_deferred_count = (
            4 if arm == "tree-native-parser-resource-committed-page" else 0
        )
        if (
            len(parsed_native_resource_tree_admissions) != 1
            or len(native_resource_tree_descriptor_lines) != 1
            or len(parsed_native_resource_tree_opens) != expected_resource_count
            or len(parsed_native_resource_tree_deferred_opens)
            != expected_deferred_count
            or len(parsed_native_resource_tree_commits) != expected_resource_count
            or len(parsed_native_resource_tree_drains) != 1
            or len(parsed_native_resource_tree_first_bodies)
            != (1 if arm == "tree-native-parser-resource-committed-page" else 0)
            or len(native_resource_tree_body_barrier_lines)
            != (1 if arm == "tree-native-parser-resource-committed-page" else 0)
        ):
            raise ValueError(
                "native parser resource-tree arm requires one early admission, "
                "one matching parser flush, the configured resource opens and "
                "commits, and one drain"
            )
        admission = parsed_native_resource_tree_admissions[0]
        drain = parsed_native_resource_tree_drains[0]
        connection = admission["connection"]
        expected_resources = {1: "style", 2: "script"}
        expected_resources.update({
            index: "image" for index in range(3, expected_resource_count + 1)
        })
        opens = {
            int(marker["stream"]): marker["kind"]
            for marker in parsed_native_resource_tree_opens
        }
        open_lifecycles = {
            int(marker["stream"]): marker["lifecycle"]
            for marker in parsed_native_resource_tree_opens
        }
        expected_open_lifecycles = {
            index: (
                "prepared"
                if arm == "tree-native-parser-resource-committed-page"
                and kind == "image"
                else "opened"
            )
            for index, kind in expected_resources.items()
        }
        deferred_opens = {
            int(marker["stream"])
            for marker in parsed_native_resource_tree_deferred_opens
        }
        expected_deferred_opens = (
            {3, 4, 5, 6}
            if arm == "tree-native-parser-resource-committed-page"
            else set()
        )
        commits = {
            int(marker["stream"]) for marker in parsed_native_resource_tree_commits
        }
        expected_admission = (
            "resources-committed"
            if arm
            in (
                "tree-native-parser-resource-committed-tree",
                "tree-native-parser-resource-committed-page",
            )
            else "request-committed"
        )
        if (
            admission["admission"] != expected_admission
            or admission["request_committed"] != "1"
            or (
                arm == "tree-native-parser-document-start-resource-tree"
                and admission["root_done"] != "0"
            )
            or admission["protocol"] != protocol
            or opens != expected_resources
            or open_lifecycles != expected_open_lifecycles
            or deferred_opens != expected_deferred_opens
            or commits != set(expected_resources)
            or any(
                marker["protocol"] != protocol
                for markers in (
                    parsed_native_resource_tree_opens,
                    parsed_native_resource_tree_deferred_opens,
                    parsed_native_resource_tree_commits,
                )
                for marker in markers
            )
            or drain["connection"] != connection
            or int(drain["completed_resources"]) != expected_resource_count
            or drain["protocol"] != protocol
            or not 200 <= int(drain["http"]) < 300
            or result["connection"] != connection
        ):
            raise ValueError("native parser resource-tree causal state is invalid")
        matching_established = [
            line
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == connection
            and established["protocol"] == protocol
        ]
        if len(matching_established) != 1:
            raise ValueError(
                "native parser resource-tree arm requires one matching CONNECT marker"
            )
        admission_index = log_lines.index(native_resource_tree_admission_lines[0])
        established_index = log_lines.index(matching_established[0])
        descriptor_index = log_lines.index(native_resource_tree_descriptor_lines[0])
        open_indices = [
            log_lines.index(line) for line in native_resource_tree_open_lines
        ]
        deferred_open_indices = [
            log_lines.index(line) for line in native_resource_tree_deferred_open_lines
        ]
        commit_indices = [
            log_lines.index(line) for line in native_resource_tree_commit_lines
        ]
        result_index = log_lines.index(result_lines[0])
        drain_index = log_lines.index(native_resource_tree_drain_lines[0])
        document_start_order = (
            admission_index < established_index < descriptor_index
            and descriptor_index < min(open_indices)
            and max(open_indices) < min(commit_indices)
            and max(commit_indices) < result_index < drain_index
        )
        resource_committed_order = (
            descriptor_index < min(open_indices)
            and max(open_indices) < min(commit_indices)
            and max(commit_indices) < admission_index < result_index
            and result_index < established_index < drain_index
        )
        if arm == "tree-native-parser-resource-committed-page":
            first_body = parsed_native_resource_tree_first_bodies[0]
            first_body_stream = int(first_body["stream"])
            first_body_index = log_lines.index(native_resource_tree_first_body_lines[0])
            body_barrier_index = log_lines.index(
                native_resource_tree_body_barrier_lines[0]
            )
            open_index_by_stream = {
                int(marker["stream"]): log_lines.index(line)
                for line, marker in zip(
                    native_resource_tree_open_lines,
                    parsed_native_resource_tree_opens,
                )
            }
            open_index_by_stream.update({
                int(marker["stream"]): log_lines.index(line)
                for line, marker in zip(
                    native_resource_tree_deferred_open_lines,
                    parsed_native_resource_tree_deferred_opens,
                )
            })
            commit_index_by_stream = {
                int(marker["stream"]): log_lines.index(line)
                for line, marker in zip(
                    native_resource_tree_commit_lines,
                    parsed_native_resource_tree_commits,
                )
            }
            resource_committed_order = (
                first_body["protocol"] == protocol
                and first_body_stream in expected_resources
                and native_resource_tree_body_barrier_lines[0].endswith(
                    "Preamble native-parser-resource-tree "
                    "barrier=first-resource-body-buffer assets=6 committed=6 "
                    f"protocol={protocol}"
                )
                and descriptor_index < min(open_indices)
                and max(open_indices) < min(deferred_open_indices)
                and all(
                    open_index_by_stream[stream] < commit_index_by_stream[stream]
                    for stream in expected_resources
                )
                and commit_index_by_stream[first_body_stream] < first_body_index
                and max(commit_indices) < body_barrier_index
                and first_body_index < body_barrier_index
                and body_barrier_index < admission_index
                and admission_index < result_index < established_index
                and result_index < drain_index
            )
        if not (
            document_start_order
            if arm == "tree-native-parser-document-start-resource-tree"
            else resource_committed_order
        ):
            raise ValueError(
                "native parser resource-tree lifecycle markers have invalid ordering"
            )
    elif any(native_resource_tree_markers) or native_resource_tree_descriptor_lines:
        raise ValueError(
            f"{arm} arm unexpectedly logged native parser resource-tree lifecycle"
        )

    if arm == "tree-native-parser-document-start-navigation-stop-css":
        if any(
            len(markers) != 1 for markers in parsed_native_parser_navigation_stop_groups
        ):
            raise ValueError(
                "native parser navigation-stop arm requires exactly one "
                "admission, stylesheet commit, response start, tunnel "
                "activity, stop, abort, and drain marker"
            )
        if (
            parsed_native_parser_discoveries
            or len(native_parser_descriptor_lines) != 1
            or parsed_native_parser_channels
            or len(native_parser_lightweight_open_lines) != 1
            or parsed_native_parser_drains
            or parsed_native_parser_admissions
            or parsed_native_parser_barriers
        ):
            raise ValueError(
                "native parser navigation-stop arm requires one parser "
                "descriptor and channel without a late parser barrier or "
                "full CSS drain"
            )
        (
            admissions,
            stylesheets,
            tunnel_active_markers,
            response_started_markers,
            stops,
            onstops,
            drains,
        ) = parsed_native_parser_navigation_stop_groups
        admission = admissions[0]
        stylesheet = stylesheets[0]
        tunnel_active = tunnel_active_markers[0]
        response_started = response_started_markers[0]
        stop = stops[0]
        onstop = onstops[0]
        drain = drains[0]
        connection = admission["connection"]
        if (
            admission["admission"] != "request-committed"
            or admission["request_committed"] != "1"
            or admission["root_done"] != "0"
            or stylesheet["stream"] != "1"
            or stylesheet["status"] != "waiting-for"
            or tunnel_active["direction"] != "client-to-target"
            or tunnel_active["bytes_positive"] != "1"
            or not 200 <= int(response_started["http"]) < 300
            or stop["reason"] != "NS_BINDING_ABORTED"
            or stop["load_group"] != "scoped"
            or onstop["status"] != "NS_BINDING_ABORTED"
            or onstop["expected"] != "1"
            or drain["root_done"] != "1"
            or drain["css_committed"] != "1"
            or drain["css_aborted"] != "1"
            or not 200 <= int(drain["http"]) < 300
            or any(
                marker["connection"] != connection or marker["protocol"] != protocol
                for marker in (
                    stylesheet,
                    tunnel_active,
                    response_started,
                    stop,
                    onstop,
                    drain,
                )
            )
            or admission["protocol"] != protocol
            or result["connection"] != connection
        ):
            raise ValueError("native parser navigation-stop causal state is invalid")
        matching_established = [
            line
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == connection
            and established["protocol"] == protocol
        ]
        if len(matching_established) != 1:
            raise ValueError(
                "native parser navigation-stop arm requires one matching CONNECT marker"
            )
        admission_index = log_lines.index(
            native_parser_navigation_stop_admission_lines[0]
        )
        established_index = log_lines.index(matching_established[0])
        descriptor_index = log_lines.index(native_parser_descriptor_lines[0])
        open_index = log_lines.index(native_parser_lightweight_open_lines[0])
        stylesheet_index = log_lines.index(
            native_parser_navigation_stop_stylesheet_lines[0]
        )
        tunnel_active_index = log_lines.index(
            native_parser_navigation_stop_tunnel_active_lines[0]
        )
        response_started_index = log_lines.index(
            native_parser_navigation_stop_response_started_lines[0]
        )
        stop_index = log_lines.index(native_parser_navigation_stop_issued_lines[0])
        onstop_index = log_lines.index(native_parser_navigation_stop_onstop_lines[0])
        result_index = log_lines.index(result_lines[0])
        drain_index = log_lines.index(native_parser_navigation_stop_drain_lines[0])
        if not (
            admission_index < established_index < tunnel_active_index
            and admission_index
            < established_index
            < descriptor_index
            < open_index
            < stylesheet_index
            < response_started_index
            and max(stylesheet_index, tunnel_active_index, response_started_index)
            < stop_index
            < onstop_index
            < result_index
            < drain_index
        ):
            raise ValueError(
                "native parser navigation-stop lifecycle markers have invalid ordering"
            )
    elif native_parser_navigation_stop_evidence_lines:
        raise ValueError(
            f"{arm} arm unexpectedly logged native parser navigation-stop lifecycle"
        )

    if arm == "tree-native-parser-document-start-response-stop-css":
        (
            admissions,
            stylesheets,
            tunnel_active_markers,
            response_started_markers,
            stops,
            onstops,
            drains,
        ) = parsed_native_parser_response_stop_groups
        common_groups = (
            admissions,
            stylesheets,
            response_started_markers,
            drains,
        )
        causal_abort_groups = (tunnel_active_markers, stops, onstops)
        if any(len(markers) != 1 for markers in common_groups) or any(
            len(markers) > 1 for markers in causal_abort_groups
        ):
            raise ValueError(
                "native parser response-stop arm requires exactly one "
                "admission, stylesheet commit, response start, and drain "
                "marker, with at most one decoded target activity, stop, "
                "and abort marker"
            )
        causal_abort = all(len(markers) == 1 for markers in causal_abort_groups)
        natural_completion = all(len(markers) == 0 for markers in causal_abort_groups)
        if not (causal_abort or natural_completion):
            raise ValueError(
                "native parser response-stop arm requires either a complete "
                "causal abort lifecycle or a natural completion without "
                "target activity or cancellation markers"
            )
        if (
            parsed_native_parser_discoveries
            or len(native_parser_descriptor_lines) != 1
            or parsed_native_parser_channels
            or len(native_parser_lightweight_open_lines) != 1
            or parsed_native_parser_admissions
            or parsed_native_parser_barriers
        ):
            raise ValueError(
                "native parser response-stop arm requires one parser "
                "descriptor and channel without a late parser barrier or "
                "full CSS drain"
            )
        if causal_abort and parsed_native_parser_drains:
            raise ValueError(
                "native parser response-stop abort branch unexpectedly "
                "completed the full CSS preload"
            )
        if natural_completion and len(parsed_native_parser_drains) != 1:
            raise ValueError(
                "native parser response-stop natural branch requires one "
                "successful full CSS preload drain"
            )
        admission = admissions[0]
        stylesheet = stylesheets[0]
        response_started = response_started_markers[0]
        drain = drains[0]
        connection = admission["connection"]
        identity_markers = (stylesheet, response_started, drain)
        if causal_abort:
            tunnel_active = tunnel_active_markers[0]
            stop = stops[0]
            onstop = onstops[0]
            identity_markers += (tunnel_active, stop, onstop)
        else:
            native_drain = parsed_native_parser_drains[0]
            identity_markers += (native_drain,)
        if (
            admission["admission"] != "request-committed"
            or admission["request_committed"] != "1"
            or admission["root_done"] != "0"
            or stylesheet["stream"] != "1"
            or stylesheet["status"] != "waiting-for"
            or not 200 <= int(response_started["http"]) < 300
            or drain["root_done"] != "1"
            or drain["css_committed"] != "1"
            or drain["css_aborted"] != ("1" if causal_abort else "0")
            or drain["css_completed"] != ("0" if causal_abort else "1")
            or not 200 <= int(drain["http"]) < 300
            or any(
                marker["connection"] != connection or marker["protocol"] != "h3"
                for marker in identity_markers
            )
            or admission["protocol"] != "h3"
            or result["connection"] != connection
            or (
                natural_completion
                and (
                    native_drain["completed_resources"] != "1"
                    or not 200 <= int(native_drain["http"]) < 300
                )
            )
            or (
                causal_abort
                and (
                    tunnel_active["direction"] != "target-to-client"
                    or tunnel_active["bytes_positive"] != "1"
                    or tunnel_active["payload"] != "decoded"
                    or stop["reason"] != "NS_BINDING_ABORTED"
                    or stop["load_group"] != "scoped"
                    or onstop["status"] != "NS_BINDING_ABORTED"
                    or onstop["expected"] != "1"
                )
            )
        ):
            raise ValueError("native parser response-stop causal state is invalid")
        matching_established = [
            line
            for line, established in zip(established_lines, parsed_established)
            if established["connection"] == connection
            and established["protocol"] == "h3"
        ]
        if len(matching_established) != 1:
            raise ValueError(
                "native parser response-stop arm requires one matching CONNECT marker"
            )
        admission_index = log_lines.index(
            native_parser_response_stop_admission_lines[0]
        )
        established_index = log_lines.index(matching_established[0])
        descriptor_index = log_lines.index(native_parser_descriptor_lines[0])
        open_index = log_lines.index(native_parser_lightweight_open_lines[0])
        stylesheet_index = log_lines.index(
            native_parser_response_stop_stylesheet_lines[0]
        )
        response_started_index = log_lines.index(
            native_parser_response_stop_response_started_lines[0]
        )
        result_index = log_lines.index(result_lines[0])
        drain_index = log_lines.index(native_parser_response_stop_drain_lines[0])
        common_order_valid = (
            admission_index
            < established_index
            < descriptor_index
            < open_index
            < stylesheet_index
            < response_started_index
        )
        if causal_abort:
            tunnel_active_index = log_lines.index(
                native_parser_response_stop_tunnel_active_lines[0]
            )
            stop_index = log_lines.index(native_parser_response_stop_issued_lines[0])
            onstop_index = log_lines.index(native_parser_response_stop_onstop_lines[0])
            ordering_valid = (
                admission_index < established_index < tunnel_active_index
                and common_order_valid
                and max(
                    stylesheet_index,
                    tunnel_active_index,
                    response_started_index,
                )
                < stop_index
                < onstop_index
                < result_index
                < drain_index
            )
        else:
            native_drain_index = log_lines.index(native_parser_drain_lines[0])
            ordering_valid = (
                common_order_valid
                and response_started_index
                < result_index
                < native_drain_index
                < drain_index
            )
        if not ordering_valid:
            raise ValueError(
                "native parser response-stop lifecycle markers have invalid ordering"
            )
    elif native_parser_response_stop_evidence_lines:
        raise ValueError(
            f"{arm} arm unexpectedly logged native parser response-stop lifecycle"
        )

    if arm == "tree-native-parser-document-handoff-overlap-css":
        if len(parsed_native_parser_document_handoffs) != len(
            NATIVE_PARSER_DOCUMENT_HANDOFF_PHASES
        ):
            raise ValueError(
                "native parser document handoff requires exactly one marker "
                "for every lifecycle phase"
            )
        phases = tuple(
            marker["phase"] for marker in parsed_native_parser_document_handoffs
        )
        if phases != NATIVE_PARSER_DOCUMENT_HANDOFF_PHASES:
            raise ValueError(
                "native parser document handoff phases are missing, duplicated, "
                "unknown, or out of order"
            )
        deliveries = tuple(
            marker["delivery"] for marker in parsed_native_parser_document_handoffs
        )
        if deliveries != (None, None, None, None, None, "main-copy-dispatch"):
            raise ValueError(
                "native parser document handoff delivery contract is invalid"
            )
        handoff_connection = parsed_native_parser_document_handoffs[0]["connection"]
        if any(
            marker["connection"] != handoff_connection or marker["protocol"] != "h3"
            for marker in parsed_native_parser_document_handoffs
        ):
            raise ValueError(
                "native parser document handoff marker identity is inconsistent"
            )
        if handoff_connection != parsed_native_parser_discoveries[0]["connection"]:
            raise ValueError(
                "native parser document handoff and preload identities differ"
            )
        ordered_lines = (
            *native_parser_document_handoff_lines,
            native_parser_discovery_lines[0],
            native_parser_channel_lines[0],
            native_parser_admission_lines[0],
            native_parser_barrier_lines[0],
            result_lines[0],
            native_parser_drain_lines[0],
        )
        ordered_indices = tuple(log_lines.index(line) for line in ordered_lines)
        if tuple(sorted(ordered_indices)) != ordered_indices or len(
            set(ordered_indices)
        ) != len(ordered_indices):
            raise ValueError(
                "native parser document handoff and preload markers have invalid "
                "ordering"
            )
    elif parsed_native_parser_document_handoffs:
        raise ValueError(
            f"{arm} arm unexpectedly logged native parser document handoff lifecycle"
        )

    if arm in (
        "tree-native-parser-retarget-overlap-css",
        "tree-native-parser-ipc-rendezvous-overlap-css",
    ):
        if len(parsed_native_parser_retargets) != len(NATIVE_PARSER_RETARGET_PHASES):
            raise ValueError(
                "native parser retarget requires exactly one marker for every "
                "lifecycle phase"
            )
        phases = tuple(marker["phase"] for marker in parsed_native_parser_retargets)
        if phases != NATIVE_PARSER_RETARGET_PHASES:
            raise ValueError(
                "native parser retarget phases are missing, duplicated, unknown, "
                "or out of order"
            )
        targets = tuple(marker["target"] for marker in parsed_native_parser_retargets)
        verified = tuple(
            marker["verified"] for marker in parsed_native_parser_retargets
        )
        deliveries = tuple(
            marker["delivery"] for marker in parsed_native_parser_retargets
        )
        if targets != (None, None, None, "html5-parser", None, None, None, None):
            raise ValueError("native parser retarget target contract is invalid")
        if verified != (None, None, None, "1", None, None, None, None):
            raise ValueError("native parser retarget verification failed")
        if deliveries != (
            None,
            None,
            None,
            None,
            None,
            None,
            "retargeted-direct",
            None,
        ):
            raise ValueError("native parser retarget delivery contract is invalid")
        retarget_connection = parsed_native_parser_retargets[0]["connection"]
        if any(
            marker["connection"] != retarget_connection or marker["protocol"] != "h3"
            for marker in parsed_native_parser_retargets
        ):
            raise ValueError("native parser retarget marker identity is inconsistent")
        if retarget_connection != parsed_native_parser_discoveries[0]["connection"]:
            raise ValueError("native parser retarget and preload identities differ")
        ordered_lines = (
            *native_parser_retarget_lines,
            native_parser_discovery_lines[0],
            native_parser_channel_lines[0],
            native_parser_admission_lines[0],
            native_parser_barrier_lines[0],
            result_lines[0],
            native_parser_drain_lines[0],
        )
        ordered_indices = tuple(log_lines.index(line) for line in ordered_lines)
        if tuple(sorted(ordered_indices)) != ordered_indices or len(
            set(ordered_indices)
        ) != len(ordered_indices):
            raise ValueError(
                "native parser retarget and preload markers have invalid ordering"
            )
    elif arm == "tree-native-parser-root-rendezvous-overlap-css":
        if len(parsed_native_parser_retargets) != len(
            NATIVE_PARSER_ROOT_RENDEZVOUS_RETARGET_PHASES
        ):
            raise ValueError(
                "native parser root rendezvous retarget requires exactly one "
                "marker for every lifecycle phase"
            )
        phases = tuple(marker["phase"] for marker in parsed_native_parser_retargets)
        if phases != NATIVE_PARSER_ROOT_RENDEZVOUS_RETARGET_PHASES:
            raise ValueError(
                "native parser root rendezvous phases are missing, duplicated, "
                "unknown, or out of order"
            )
        targets = tuple(marker["target"] for marker in parsed_native_parser_retargets)
        verified = tuple(
            marker["verified"] for marker in parsed_native_parser_retargets
        )
        deliveries = tuple(
            marker["delivery"] for marker in parsed_native_parser_retargets
        )
        if targets != ("html5-parser", None, None):
            raise ValueError(
                "native parser root rendezvous retarget target contract is invalid"
            )
        if verified != ("1", None, None):
            raise ValueError(
                "native parser root rendezvous retarget verification failed"
            )
        if deliveries != (None, "logical-background", None):
            raise ValueError(
                "native parser root rendezvous delivery contract is invalid"
            )
        retarget_connection = parsed_native_parser_retargets[0]["connection"]
        if any(
            marker["connection"] != retarget_connection or marker["protocol"] != "h3"
            for marker in parsed_native_parser_retargets
        ):
            raise ValueError(
                "native parser root rendezvous marker identity is inconsistent"
            )
        if retarget_connection != parsed_native_parser_discoveries[0]["connection"]:
            raise ValueError(
                "native parser root rendezvous and preload identities differ"
            )
        ordered_lines = (
            *native_parser_retarget_lines,
            native_parser_discovery_lines[0],
            native_parser_channel_lines[0],
            native_parser_admission_lines[0],
            native_parser_barrier_lines[0],
            result_lines[0],
            native_parser_drain_lines[0],
        )
        ordered_indices = tuple(log_lines.index(line) for line in ordered_lines)
        if tuple(sorted(ordered_indices)) != ordered_indices or len(
            set(ordered_indices)
        ) != len(ordered_indices):
            raise ValueError(
                "native parser root rendezvous and preload markers have invalid "
                "ordering"
            )
    elif parsed_native_parser_retargets:
        raise ValueError(
            f"{arm} arm unexpectedly logged native parser retarget lifecycle"
        )

    if arm == "tree-native-parser-root-rendezvous-overlap-css":
        if len(parsed_native_parser_root_replacements) != len(
            NATIVE_PARSER_ROOT_REPLACEMENT_PHASES
        ):
            raise ValueError(
                "native parser root replacement requires exactly one marker "
                "for every product lifecycle phase"
            )
        product_phases = tuple(
            marker["phase"] for marker in parsed_native_parser_root_replacements
        )
        if product_phases != NATIVE_PARSER_ROOT_REPLACEMENT_PHASES:
            raise ValueError(
                "native parser root replacement product phases are missing, "
                "duplicated, unknown, or out of order"
            )
        first_product = parsed_native_parser_root_replacements[0]
        product_connection = first_product["connection"]
        product_channel = first_product["channel"]
        product_generation = first_product["generation"]
        registered_index = NATIVE_PARSER_ROOT_REPLACEMENT_PHASES.index(
            "replacement-registered"
        )
        product_request = parsed_native_parser_root_replacements[registered_index][
            "request"
        ]
        product_identity = (
            product_connection,
            product_channel,
            product_request,
            product_generation,
        )
        if (
            any(value == "0" for value in product_identity)
            or any(
                marker["connection"] != product_connection
                or marker["channel"] != product_channel
                or marker["generation"] != product_generation
                or marker["protocol"] != "h3"
                for marker in parsed_native_parser_root_replacements
            )
            or any(
                marker["request"] != "0"
                for marker in parsed_native_parser_root_replacements[:registered_index]
            )
            or any(
                marker["request"] != product_request
                for marker in parsed_native_parser_root_replacements[registered_index:]
            )
        ):
            raise ValueError(
                "native parser root replacement registration identity differs"
            )
        if product_identity[0] != parsed_native_parser_discoveries[0]["connection"]:
            raise ValueError(
                "native parser root replacement and preload identities differ"
            )
        phases = [marker["phase"] for marker in parsed_native_root_activations]
        allowed_phases = NATIVE_ROOT_PLAIN_PHASES | {
            "connect-parent-linked",
            "redirect-verification-run",
            "redirect-verification-callback",
            "redirect-verification-resolved",
            "forward-sent",
            "forward-received",
            "forward-data-received",
            "forward-data",
            "forward-stop-received",
            "forward-stop",
            "activation-released",
            "on-stop",
        }
        if any(phase not in allowed_phases for phase in phases):
            raise ValueError(
                "native root replacement emitted failure, cancellation, or "
                "unknown request evidence"
            )
        request = parsed_native_root_activations[0]["request"] if phases else None
        if not request or any(
            marker["request"] != request for marker in parsed_native_root_activations
        ):
            raise ValueError("native root replacement request identity differs")
        if request != product_identity[2]:
            raise ValueError("native root bridge and product request identities differ")

        by_phase = {}
        channel_identity = None
        for line, marker in zip(
            native_root_activation_evidence_lines,
            parsed_native_root_activations,
        ):
            phase = marker["phase"]
            suffix = marker["suffix"]
            by_phase.setdefault(phase, []).append(log_lines.index(line))
            identity = None
            if phase in NATIVE_ROOT_PLAIN_PHASES:
                if suffix:
                    raise ValueError(
                        f"native root replacement {phase} marker is malformed"
                    )
            elif phase == "connect-parent-linked":
                if suffix != " same_channel=1":
                    raise ValueError(
                        "native root replacement did not link the same root channel"
                    )
            elif phase in (
                "redirect-verification-run",
                "forward-sent",
                "forward-received",
            ):
                match = re.fullmatch(r" channel=(\d+) generation=(\d+)", suffix)
                if not match:
                    raise ValueError(
                        f"native root replacement {phase} marker is malformed"
                    )
                identity = match.groups()
            elif phase in (
                "redirect-verification-callback",
                "redirect-verification-resolved",
            ):
                match = re.fullmatch(
                    r" channel=(\d+) generation=(\d+) status=0x00000000",
                    suffix,
                )
                if not match:
                    raise ValueError(
                        f"native root replacement {phase} did not complete cleanly"
                    )
                identity = match.groups()
            elif phase in ("forward-data-received", "forward-data"):
                if not re.fullmatch(r" bytes=\d+", suffix):
                    raise ValueError(
                        f"native root replacement {phase} marker is malformed"
                    )
            elif phase in ("forward-stop-received", "forward-stop"):
                if suffix != " status=0x00000000":
                    raise ValueError(
                        f"native root replacement {phase} did not complete cleanly"
                    )
            elif phase == "activation-released":
                if suffix != " status=0x00000000":
                    raise ValueError(
                        "native root replacement activation did not release cleanly"
                    )
            elif phase == "on-stop":
                match = re.fullmatch(r" status=0x00000000 generation=(\d+)", suffix)
                if not match:
                    raise ValueError(
                        "native root replacement physical root did not stop cleanly"
                    )
                identity = (None, match.group(1))
            if identity:
                if identity[0] is not None:
                    if channel_identity is None:
                        channel_identity = identity
                    elif channel_identity != identity:
                        raise ValueError(
                            "native root replacement channel/generation identity differs"
                        )
                elif channel_identity is None or channel_identity[1] != identity[1]:
                    raise ValueError(
                        "native root replacement completion generation differs"
                    )
        if channel_identity != (product_identity[1], product_identity[3]):
            raise ValueError("native root bridge and product channel/generation differ")

        for phase in NATIVE_ROOT_REQUIRED_PLAIN_PHASES | {
            "connect-parent-linked",
            "redirect-verification-run",
            "redirect-verification-callback",
            "redirect-verification-resolved",
            "forward-sent",
            "forward-received",
            "forward-stop-received",
            "forward-stop",
            "activation-released",
            "on-stop",
        }:
            count = len(by_phase.get(phase, ()))
            if (phase == "continue-verification" and count < 1) or (
                phase != "continue-verification" and count != 1
            ):
                raise ValueError(
                    "native root replacement phases are missing, duplicated, or unknown"
                )
        if len(by_phase["continue-verification"]) != (
            len(by_phase.get("background-wait", ())) + 1
        ):
            raise ValueError(
                "native root replacement background wait/continue contract is invalid"
            )
        data_received = by_phase.get("forward-data-received", ())
        data_delivered = by_phase.get("forward-data", ())
        if not data_received or len(data_received) != len(data_delivered):
            raise ValueError(
                "native root replacement DATA forwarding is missing or unpaired"
            )

        first = lambda phase: by_phase[phase][0]
        last = lambda phase: by_phase[phase][-1]
        product_by_phase = {
            marker["phase"]: log_lines.index(line)
            for line, marker in zip(
                native_parser_root_replacement_evidence_lines,
                parsed_native_parser_root_replacements,
            )
        }
        if not (
            first("descriptor-frozen")
            < first("request-primary-actor-created")
            < first("request-primary-actor-bound")
            < first("begin-received")
            and first("request-primary-actor-created")
            < first("begin-sent")
            < first("begin-received")
            < first("connect-parent-sent")
            < first("redirect-verification-started")
            < first("connect-parent-linked")
            < first("redirect-verification-queued")
            < first("redirect-verification-run")
            < first("redirect-verification-callback")
            < first("redirect-verification-resolved")
            and first("begin-received") < first("background-dispatched")
            and first("begin-received")
            < first("request-background-actor-created")
            < first("request-background-actor-bound")
            < first("background-ready")
            < first("bg-linked")
            and first("connect-parent-linked") < first("continue-verification")
            and first("redirect-verification-resolved") < first("continue-verification")
            and first("bg-linked")
            < last("continue-verification")
            < first("ready-to-verify")
            < first("setup-finished")
            < first("forward-sent")
            < first("activation-released")
            < first("resume")
            < first("forward-stop-received")
            < first("forward-stop")
            < first("on-stop")
            and first("resume")
            < first("forward-received")
            < first("forward-start")
            < first("forward-data-received")
            < first("forward-data")
            and last("forward-data-received") < first("forward-stop-received")
            and last("forward-data") < first("forward-stop")
            and first("on-stop")
            < first("request-primary-actor-delete-sent")
            < first("request-primary-actor-destroyed")
            and first("on-stop")
            < first("request-background-actor-delete-sent")
            < first("request-background-actor-destroyed")
        ):
            raise ValueError(
                "native root replacement rendezvous has invalid causal ordering"
            )
        waits = by_phase.get("background-wait", ())
        continues = by_phase["continue-verification"]
        if waits and not (
            all(continues[index] < wait for index, wait in enumerate(waits))
            and all(wait < first("background-ready") for wait in waits)
            and first("background-ready") < continues[-1]
        ):
            raise ValueError(
                "native root replacement background wait has invalid ordering"
            )
        retarget_delivery_index = log_lines.index(native_parser_retarget_lines[0])
        if not (
            product_by_phase["root-response-validated"]
            < product_by_phase["physical-root-suspended"]
            < first("descriptor-frozen")
            < product_by_phase["replacement-registered"]
            and first("redirect-verification-started")
            < product_by_phase["connect-parent-same-root-linked"]
            < product_by_phase["redirect-verifier-run-queued"]
            and first("redirect-verification-run")
            < product_by_phase["redirect-verifier-run"]
            < product_by_phase["redirect-verifier-callback-queued"]
            and first("redirect-verification-resolved")
            < product_by_phase["redirect-verifier-callback-resolved"]
            and first("setup-finished")
            < first("forward-sent")
            < first("activation-released")
            < product_by_phase["replacement-listener-published"]
            < product_by_phase["forward-on-start-sent"]
            < product_by_phase["physical-root-resume"]
            < first("resume")
            < first("forward-received")
            < first("forward-start")
            < product_by_phase["forward-on-start-received"]
            < product_by_phase["consumer-constructed-main"]
            < retarget_delivery_index
            < product_by_phase["logical-request-retargeted"]
        ):
            raise ValueError(
                "native root replacement product/bridge handoff ordering is invalid"
            )
    elif parsed_native_root_activations or parsed_native_parser_root_replacements:
        raise ValueError(
            f"{arm} arm unexpectedly logged native root replacement lifecycle"
        )

    if arm in (
        "tree-native-parser-ipc-rendezvous-overlap-css",
        "tree-native-parser-root-rendezvous-overlap-css",
    ):
        if len(native_parser_descriptor_lines) != 1:
            raise ValueError(
                "native style activation requires one successful parser "
                "descriptor flush"
            )
        if len(parsed_native_style_activations) != len(NATIVE_STYLE_ACTIVATION_PHASES):
            raise ValueError(
                "native style activation requires exactly one marker for every "
                "request lifecycle phase"
            )
        phases = tuple(marker["phase"] for marker in parsed_native_style_activations)
        if set(phases) != NATIVE_STYLE_ACTIVATION_PHASES:
            raise ValueError(
                "native style activation phases are missing, duplicated, or unknown"
            )
        request = parsed_native_style_activations[0]["request"]
        if any(
            marker["request"] != request for marker in parsed_native_style_activations
        ):
            raise ValueError("native style activation request identity differs")
        by_phase = {
            marker["phase"]: log_lines.index(line)
            for line, marker in zip(
                native_style_activation_lines,
                parsed_native_style_activations,
            )
        }
        if any(
            parsed_native_style_activations[phases.index(phase)]["status"]
            != "0x00000000"
            for phase in ("activation-released", "on-stop")
        ):
            raise ValueError("native style activation release or completion failed")
        if any(
            marker["status"] is not None
            for marker in parsed_native_style_activations
            if marker["phase"] not in ("activation-released", "on-stop")
        ):
            raise ValueError("native style activation status contract is invalid")
        if not (
            by_phase["descriptor-frozen"]
            < by_phase["request-primary-actor-created"]
            < by_phase["request-primary-actor-bound"]
            < by_phase["child-open-sent"]
            < by_phase["parent-channel-created"]
            < by_phase["activation-released"]
            and by_phase["descriptor-frozen"]
            < by_phase["background-dispatched"]
            < by_phase["request-background-actor-created"]
            < by_phase["request-background-actor-bound"]
            < by_phase["bg-ready-sent"]
            < by_phase["background-ready-received"]
            < by_phase["activation-released"]
            and by_phase["activation-released"]
            < by_phase["async-open"]
            < by_phase["on-stop"]
            and by_phase["on-stop"]
            < by_phase["request-primary-actor-delete-sent"]
            < by_phase["request-primary-actor-destroyed"]
            and by_phase["on-stop"]
            < by_phase["request-background-actor-delete-sent"]
            < by_phase["request-background-actor-destroyed"]
            and log_lines.index(native_parser_descriptor_lines[0])
            < by_phase["descriptor-frozen"]
        ):
            raise ValueError(
                "native style activation rendezvous has invalid causal ordering"
            )
        if (
            len(native_style_channel_created_lines) != 1
            or len(native_style_opened_lines) != 1
        ):
            raise ValueError(
                "native style activation requires one channel-created and "
                "stylesheet-opened marker"
            )
        if not (
            log_lines.index(native_style_channel_created_lines[0])
            < by_phase["activation-released"]
            < by_phase["async-open"]
            and by_phase["activation-released"]
            < log_lines.index(native_style_opened_lines[0])
            < log_lines.index(native_parser_channel_lines[0])
        ):
            raise ValueError(
                "native style activation channel/open markers have invalid ordering"
            )
    elif (
        parsed_native_style_activations
        or native_style_channel_created_lines
        or native_style_opened_lines
    ):
        raise ValueError(
            f"{arm} arm unexpectedly logged native style activation request lifecycle"
        )

    if arm != "off":
        if feature_document.get("protocol") != protocol:
            raise ValueError("feature document protocol does not match sample")
        connections = feature_document.get("features", {}).get(
            "lifecycle_connection_count"
        )
        if connections != 1.0:
            raise ValueError(
                f"{arm} arm requires one physical outer connection, got {connections}"
            )
        if (
            arm
            in (
                "tree-native-parser-document-handoff-overlap-css",
                "tree-native-parser-document-start-overlap-css",
                "tree-native-parser-document-start-resource-tree",
                "tree-native-parser-resource-committed-tree",
                "tree-native-parser-resource-committed-page",
                "tree-native-parser-document-start-navigation-stop-css",
                "tree-native-parser-document-start-response-stop-css",
                "tree-native-parser-retarget-overlap-css",
                "tree-native-parser-ipc-rendezvous-overlap-css",
                "tree-native-parser-root-rendezvous-overlap-css",
                "tree-native-parser-process-overlap-css",
                "tree-native-parser-full-process-overlap-css",
            )
            and feature_document.get("features", {}).get("tls_client_hello_count")
            != 1.0
        ):
            raise ValueError(f"{arm} requires exactly one outer ClientHello")
    if arm in (
        "tree-native-parser-process-overlap-css",
        "tree-native-parser-full-process-overlap-css",
    ):
        validate_native_parser_process(
            log_lines,
            int(result["connection"]),
            "full-process"
            if arm == "tree-native-parser-full-process-overlap-css"
            else "process",
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--arm",
        choices=(
            "off",
            "gate",
            "root",
            "root-pmtud-control",
            "document-complete",
            "document-carrier-dispatch",
            "document-cold-winner-handoff",
            "document-native-cache-open",
            "document-native-channel-open",
            "document-handshake-confirmed",
            "document-first-buffer-overlap",
            "document-first-buffer-task-overlap",
            "document-first-buffer-task-delayed-padding",
            "document-first-buffer-task-optimistic",
            "document-first-buffer-task-http-connect",
            "document-first-buffer-http-connect",
            "document-first-buffer-http-connect-delayed-padding",
            "document-first-buffer-http-connect-optimistic",
            "document-overlap",
            "document-headers-task-overlap",
            "document-headers-task-http-connect",
            "document-overlap-http-connect",
            "document-start-http-connect",
            "document-start-overlap",
            "document-start-task-overlap",
            "document-start-task-http-connect",
            "tree-complete",
            "tree-complete-css",
            "tree-complete-resource-tree",
            "tree-early-overlap",
            "tree-early-overlap-resource-tree",
            "tree-root-overlap",
            "tree-root-overlap-css",
            "tree-resource-committed-overlap-css",
            "tree-resource-committed-overlap-tree",
            "tree-resource-committed-overlap-page",
            "tree-resource-native-cache-committed-overlap",
            "tree-native-parser-preload-overlap-css",
            "tree-native-parser-document-start-overlap-css",
            "tree-native-parser-document-start-resource-tree",
            "tree-native-parser-resource-committed-tree",
            "tree-native-parser-resource-committed-page",
            "tree-native-parser-resource-committed-page-http-connect",
            "tree-native-parser-document-start-navigation-stop-css",
            "tree-native-parser-document-start-response-stop-css",
            "tree-native-parser-document-handoff-overlap-css",
            "tree-native-parser-retarget-overlap-css",
            "tree-native-parser-ipc-rendezvous-overlap-css",
            "tree-native-parser-root-rendezvous-overlap-css",
            "tree-native-parser-process-overlap-css",
            "tree-native-parser-full-process-overlap-css",
            "tree-warm-css-304",
            "tree-overlap",
        ),
        required=True,
    )
    parser.add_argument("--protocol", choices=("h2", "h3"), required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--features", required=True)
    args = parser.parse_args()
    with open(args.log, encoding="utf-8", errors="replace") as stream:
        log_text = stream.read()
    with open(args.features, encoding="utf-8") as stream:
        feature_document = json.load(stream)
    try:
        validate_sample(args.arm, args.protocol, log_text, feature_document)
    except ValueError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
