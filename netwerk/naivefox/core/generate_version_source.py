#!/usr/bin/env python3

import re


def main(output, version_path):
    with open(version_path, encoding="utf-8") as version_file:
        version = version_file.read().strip()
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+-]*", version):
        raise ValueError("invalid NaiveFox version")
    output.write(
        '#include "NaiveFoxAPI.h"\n\n'
        'extern "C" NAIVEFOX_EXPORT const char* NaiveFoxVersion(void) {\n'
        f'  return "{version}";\n'
        '}\n'
    )
