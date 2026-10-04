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


def main(path: pathlib.Path) -> None:
    if not path.is_file():
        sys.exit(f"not a file: {path}")
    sources = typing.cast(dict[str, Source], json.loads(path.read_text()))

    qualified = typing.cast(dict[str, list[str]], json.load(sys.stdin))
    for name, sites in qualified.items():
        source = sources.setdefault(name, {})
        source["via"] = sorted({*source.get("via", []), *sites})

    write_sources(path, sources)


if __name__ == "__main__":
    match sys.argv[1:]:
        case [path]:
            main(pathlib.Path(path))
        case _:
            sys.exit("combine.py <sources.json> < qualified.json")
