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

from agents.nix import Source, write_sources


def main(path: pathlib.Path, inputs: list[str]) -> None:
    if not path.is_file():
        sys.exit(f"not a file: {path}")
    sources = typing.cast(dict[str, Source], json.loads(path.read_text()))
    held = {
        old: now
        for now, source in sources.items()
        for old in source.get("was", [])
    }
    for input in inputs:
        data = typing.cast(
            dict[str, typing.Any],
            json.load(sys.stdin)
            if input == "-"
            else json.loads(pathlib.Path(input).read_text()),
        )
        if "site" in data:
            site = typing.cast(str, data["site"])
            names = typing.cast(list[str], data["repositories"])
            listed = ((held.get(name, name), [site]) for name in names)
        else:
            listed = typing.cast(dict[str, list[str]], data).items()
        for name, sites in listed:
            source = sources.setdefault(name, {})
            source["via"] = sorted({*source.get("via", []), *sites})

    write_sources(path, sources)


if __name__ == "__main__":
    match sys.argv[1:]:
        case [path, *inputs] if inputs:
            main(pathlib.Path(path), inputs)
        case _:
            sys.exit(
                "combine.py <sources.json> <scan.json|qualified.json|->..."
            )
