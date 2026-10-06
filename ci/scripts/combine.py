#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.15"
# dependencies = ["agents.nix"]
#
# [tool.uv.sources]
# "agents.nix" = { path = "../lib", editable = true }
#
# [tool.ty.rules]
# all = "error"
# ///

import json
import pathlib
import sys
import typing

from agents.nix import Source, format_sources


def main(path: pathlib.Path, input: str, scans: list[pathlib.Path]) -> None:
    if not path.is_file():
        sys.exit(f"not a file: {path}")
    sources = typing.cast(dict[str, Source], json.loads(path.read_text()))

    qualified = typing.cast(
        dict[str, list[str]],
        json.load(sys.stdin)
        if input == "-"
        else json.loads(pathlib.Path(input).read_text()),
    )
    for scan in scans:
        listed = typing.cast(
            dict[str, typing.Any], json.loads(scan.read_text())
        )
        for name in listed["repositories"]:
            qualified.setdefault(name, []).append(listed["site"])

    held = {old: now for now, s in sources.items() for old in s.get("was", [])}
    for name, sites in qualified.items():
        source = sources.setdefault(held.get(name, name), {})
        source["via"] = sorted({*source.get("via", []), *sites})

    sys.stdout.write(format_sources(sources))


if __name__ == "__main__":
    match sys.argv[1:]:
        case [path, input, *scans]:
            main(pathlib.Path(path), input, [pathlib.Path(s) for s in scans])
        case _:
            sys.exit(
                "combine.py <sources.json> <qualified.json|-> [<scan.json>...]"
            )
