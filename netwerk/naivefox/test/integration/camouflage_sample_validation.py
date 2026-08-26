#!/usr/bin/env python3

import argparse
import json
import re

PREAMBLE_RESULT = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"preamble result=(?P<result>\S+) "
    r"status=0x[0-9a-fA-F]+ "
    r"http=(?P<http>\d+) bytes=(?P<bytes>\d+) "
    r"protocol=(?P<protocol>h2|h3)$"
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
NATIVE_STYLE_ACTIVATION = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Native style activation "
    r"phase=(?P<phase>descriptor-frozen|child-open-sent|"
    r"background-dispatched|parent-channel-created|"
    r"background-ready-received|activation-released|async-open) "
    r"request=(?P<request>\d+)"
    r"(?: status=(?P<status>0x[0-9a-fA-F]+))?$"
)
NATIVE_STYLE_ACTIVATION_PHASES = {
    "descriptor-frozen",
    "child-open-sent",
    "background-dispatched",
    "parent-channel-created",
    "background-ready-received",
    "activation-released",
    "async-open",
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
NATIVE_STYLE_BG_READY_SENT = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Native style activation "
    r"phase=bg-ready-sent request=(?P<request>\d+)$"
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
ESTABLISHED = re.compile(
    r"^(?:\[[^\]\r\n]+\] )?Connection (?P<connection>\d+) "
    r"established target=\S+ outer=(?P<protocol>h2|h3) padding=yes$"
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
        "document-overlap",
        "document-start-overlap",
        "tree-complete",
        "tree-complete-css",
        "tree-early-overlap",
        "tree-root-overlap",
        "tree-root-overlap-css",
        "tree-resource-committed-overlap-css",
        "tree-resource-native-cache-committed-overlap",
        "tree-native-parser-preload-overlap-css",
        "tree-native-parser-document-handoff-overlap-css",
        "tree-native-parser-retarget-overlap-css",
        "tree-native-parser-ipc-rendezvous-overlap-css",
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
    if arm == "document-native-channel-open" and protocol != "h3":
        raise ValueError("document-native-channel-open requires h3")
    if arm == "tree-resource-committed-overlap-css" and protocol != "h3":
        raise ValueError("tree-resource-committed-overlap-css requires h3")
    if (
        arm == "tree-resource-native-cache-committed-overlap"
        and protocol != "h3"
    ):
        raise ValueError(
            "tree-resource-native-cache-committed-overlap requires h3"
        )
    if arm == "tree-native-parser-preload-overlap-css" and protocol != "h3":
        raise ValueError("tree-native-parser-preload-overlap-css requires h3")
    if (
        arm == "tree-native-parser-document-handoff-overlap-css"
        and protocol != "h3"
    ):
        raise ValueError(
            "tree-native-parser-document-handoff-overlap-css requires h3"
        )
    if arm == "tree-native-parser-retarget-overlap-css" and protocol != "h3":
        raise ValueError("tree-native-parser-retarget-overlap-css requires h3")
    if (
        arm == "tree-native-parser-ipc-rendezvous-overlap-css"
        and protocol != "h3"
    ):
        raise ValueError(
            "tree-native-parser-ipc-rendezvous-overlap-css requires h3"
        )

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
        "document-overlap",
        "document-start-overlap",
        "tree-complete",
        "tree-complete-css",
        "tree-early-overlap",
        "tree-root-overlap",
        "tree-root-overlap-css",
        "tree-resource-committed-overlap-css",
        "tree-resource-native-cache-committed-overlap",
        "tree-native-parser-preload-overlap-css",
        "tree-native-parser-document-handoff-overlap-css",
        "tree-native-parser-retarget-overlap-css",
        "tree-native-parser-ipc-rendezvous-overlap-css",
        "tree-warm-css-304",
        "tree-overlap",
    )
    overlapping_arms = (
        "document-overlap",
        "document-start-overlap",
        "tree-early-overlap",
        "tree-root-overlap",
        "tree-root-overlap-css",
        "tree-resource-committed-overlap-css",
        "tree-resource-native-cache-committed-overlap",
        "tree-native-parser-preload-overlap-css",
        "tree-native-parser-document-handoff-overlap-css",
        "tree-native-parser-retarget-overlap-css",
        "tree-native-parser-ipc-rendezvous-overlap-css",
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
        RESOURCE_COMMITTED_DRAIN.fullmatch(line)
        for line in resource_commit_drain_lines
    ]
    if any(marker is None for marker in parsed_resource_commit_drains):
        raise ValueError("malformed resource-committed drain evidence")
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
        line
        for line in log_lines
        if " preamble native-parser-preload parser=" in line
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
        and line.endswith(" protocol=h3")
    ]
    native_parser_channel_lines = [
        line
        for line in log_lines
        if " preamble native-parser-preload channel=" in line
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
        line
        for line in log_lines
        if " preamble native-parser-preload barrier=" in line
    ]
    parsed_native_parser_barriers = [
        NATIVE_PARSER_PRELOAD_BARRIER.fullmatch(line)
        for line in native_parser_barrier_lines
    ]
    if any(marker is None for marker in parsed_native_parser_barriers):
        raise ValueError("malformed native parser preload barrier evidence")
    native_parser_drain_lines = [
        line
        for line in log_lines
        if " preamble native-parser-preload drain=" in line
    ]
    parsed_native_parser_drains = [
        NATIVE_PARSER_PRELOAD_DRAIN.fullmatch(line)
        for line in native_parser_drain_lines
    ]
    if any(marker is None for marker in parsed_native_parser_drains):
        raise ValueError("malformed native parser preload drain evidence")
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
        line
        for line in log_lines
        if " preamble native-parser-retarget " in line
    ]
    native_parser_retarget_lines = [
        line
        for line in native_parser_retarget_evidence_lines
        if " preamble native-parser-retarget phase=" in line
    ]
    parsed_native_parser_retargets = [
        NATIVE_PARSER_RETARGET.fullmatch(line)
        for line in native_parser_retarget_lines
    ]
    if any(marker is None for marker in parsed_native_parser_retargets):
        raise ValueError("malformed native parser retarget evidence")
    if len(native_parser_retarget_evidence_lines) != len(
        native_parser_retarget_lines
    ):
        raise ValueError(
            "native parser retarget emitted fallback, failure, or unknown evidence"
        )
    native_style_activation_evidence_lines = [
        line
        for line in log_lines
        if "Native style activation phase=" in line and " request=" in line
        and not NATIVE_STYLE_BG_READY_SENT.fullmatch(line)
    ]
    native_style_bg_ready_sent = [
        NATIVE_STYLE_BG_READY_SENT.fullmatch(line)
        for line in log_lines
        if NATIVE_STYLE_BG_READY_SENT.fullmatch(line)
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
    established_lines = [line for line in log_lines if " established target=" in line]
    parsed_established = [ESTABLISHED.fullmatch(line) for line in established_lines]
    if any(established is None for established in parsed_established):
        raise ValueError("malformed CONNECT-established evidence")
    native_cache_lines = [
        line for line in log_lines if " preamble native-cache-open cache=" in line
    ]
    parsed_native_cache = [NATIVE_CACHE_OPEN.fullmatch(line) for line in native_cache_lines]
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
            log_lines.index(native_cache_lines[0]) < log_lines.index(result_lines[0])
            < log_lines.index(matching_established[0][0])
        ):
            raise ValueError("native cache-open lifecycle markers have invalid ordering")
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
            log_lines.index(native_channel_lines[0]) < log_lines.index(result_lines[0])
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
            raise ValueError(
                "cold winner-handoff marker identity differs from CONNECT"
            )
        if not (
            log_lines.index(cold_winner_lines[0]) < log_lines.index(result_lines[0])
            < log_lines.index(matching_established[0][0])
        ):
            raise ValueError("cold winner-handoff markers have invalid ordering")
    elif parsed_cold_winner:
        raise ValueError(f"{arm} arm unexpectedly logged cold winner lifecycle")
    if arm == "document-overlap":
        if len(parsed_document_admissions) != 1:
            raise ValueError(
                "document-overlap requires exactly one causal admission marker"
            )
        admission = parsed_document_admissions[0]
        if (
            admission["admission"] != "response-headers"
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
        if not (
            admission_index < result_index < drain_index
            and result_index < established_index
        ):
            raise ValueError("document-overlap lifecycle markers have invalid ordering")
    elif parsed_document_admissions or parsed_document_drains:
        raise ValueError(f"{arm} arm unexpectedly logged document-overlap lifecycle")
    if arm == "document-start-overlap":
        if len(parsed_document_start_admissions) != 1:
            raise ValueError(
                "document-start-overlap requires exactly one causal admission marker"
            )
        admission = parsed_document_start_admissions[0]
        if (
            admission["admission"] != "request-committed"
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

    if arm == "tree-resource-committed-overlap-css":
        if len(parsed_resource_commit_admissions) != 1:
            raise ValueError(
                "resource-committed arm requires one causal admission marker"
            )
        if len(parsed_resource_commit_drains) != 1:
            raise ValueError("resource-committed arm requires one drain marker")
        admission = parsed_resource_commit_admissions[0]
        drain = parsed_resource_commit_drains[0]
        if (
            admission["admission"] != "request-committed"
            or admission["root_done"] != "1"
            or admission["started_resources"] != "1"
            or admission["committed_resources"] != "1"
            or admission["protocol"] != "h3"
            or drain["completed_resources"] != "1"
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
        raise ValueError(
            f"{arm} arm unexpectedly logged resource-committed lifecycle"
        )

    if arm == "tree-resource-native-cache-committed-overlap":
        if len(parsed_resource_native_cache_admissions) != 1:
            raise ValueError(
                "native resource cache arm requires one causal admission marker"
            )
        if len(parsed_resource_native_cache_drains) != 1:
            raise ValueError(
                "native resource cache arm requires one drain marker"
            )
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
            raise ValueError(
                "native resource cache markers have invalid ordering"
            )
    elif (
        parsed_resource_native_cache_admissions
        or parsed_resource_native_cache_drains
    ):
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
    if arm in (
        "tree-native-parser-preload-overlap-css",
        "tree-native-parser-document-handoff-overlap-css",
        "tree-native-parser-retarget-overlap-css",
        "tree-native-parser-ipc-rendezvous-overlap-css",
    ):
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
                marker["connection"] != connection
                or marker["protocol"] != "h3"
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
        raise ValueError(
            f"{arm} arm unexpectedly logged native parser preload lifecycle"
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
            marker["connection"] != handoff_connection
            or marker["protocol"] != "h3"
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
        if (
            tuple(sorted(ordered_indices)) != ordered_indices
            or len(set(ordered_indices)) != len(ordered_indices)
        ):
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
        if len(parsed_native_parser_retargets) != len(
            NATIVE_PARSER_RETARGET_PHASES
        ):
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
            marker["connection"] != retarget_connection
            or marker["protocol"] != "h3"
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
        if (
            tuple(sorted(ordered_indices)) != ordered_indices
            or len(set(ordered_indices)) != len(ordered_indices)
        ):
            raise ValueError(
                "native parser retarget and preload markers have invalid ordering"
            )
    elif parsed_native_parser_retargets:
        raise ValueError(
            f"{arm} arm unexpectedly logged native parser retarget lifecycle"
        )

    if arm == "tree-native-parser-ipc-rendezvous-overlap-css":
        if len(native_parser_descriptor_lines) != 1:
            raise ValueError(
                "native style activation requires one successful parser "
                "descriptor flush"
            )
        if len(parsed_native_style_activations) != len(
            NATIVE_STYLE_ACTIVATION_PHASES
        ):
            raise ValueError(
                "native style activation requires exactly one marker for every "
                "request lifecycle phase"
            )
        phases = tuple(
            marker["phase"] for marker in parsed_native_style_activations
        )
        if set(phases) != NATIVE_STYLE_ACTIVATION_PHASES:
            raise ValueError(
                "native style activation phases are missing, duplicated, or unknown"
            )
        request = parsed_native_style_activations[0]["request"]
        if any(
            marker["request"] != request
            for marker in parsed_native_style_activations
        ):
            raise ValueError("native style activation request identity differs")
        if len(native_style_bg_ready_sent) > 1 or any(
            marker["request"] != request for marker in native_style_bg_ready_sent
        ):
            raise ValueError("native style activation bg-ready identity differs")
        by_phase = {
            marker["phase"]: log_lines.index(line)
            for line, marker in zip(
                native_style_activation_lines,
                parsed_native_style_activations,
            )
        }
        if parsed_native_style_activations[phases.index("activation-released")][
            "status"
        ] != "0x00000000":
            raise ValueError("native style activation did not release successfully")
        if any(
            marker["status"] is not None
            for marker in parsed_native_style_activations
            if marker["phase"] != "activation-released"
        ):
            raise ValueError("native style activation status contract is invalid")
        if not (
            by_phase["descriptor-frozen"]
            < by_phase["child-open-sent"]
            < by_phase["parent-channel-created"]
            < by_phase["activation-released"]
            and by_phase["descriptor-frozen"]
            < by_phase["background-dispatched"]
            < by_phase["background-ready-received"]
            < by_phase["activation-released"]
            and by_phase["activation-released"] < by_phase["async-open"]
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
        or native_style_bg_ready_sent
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
                "tree-native-parser-retarget-overlap-css",
                "tree-native-parser-ipc-rendezvous-overlap-css",
            )
            and feature_document.get("features", {}).get(
                "tls_client_hello_count"
            )
            != 1.0
        ):
            raise ValueError(
                f"{arm} requires exactly one outer ClientHello"
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
            "document-overlap",
            "document-start-overlap",
            "tree-complete",
            "tree-complete-css",
            "tree-early-overlap",
            "tree-root-overlap",
            "tree-root-overlap-css",
            "tree-resource-committed-overlap-css",
            "tree-resource-native-cache-committed-overlap",
            "tree-native-parser-preload-overlap-css",
            "tree-native-parser-document-handoff-overlap-css",
            "tree-native-parser-retarget-overlap-css",
            "tree-native-parser-ipc-rendezvous-overlap-css",
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
