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
import logging
import pathlib
import sys
import typing

from agents.nix import Source, configure_logging, write_sources

log = logging.getLogger(__name__)


def main(path: pathlib.Path, scans: list[pathlib.Path]) -> None:
    if not path.is_file():
        sys.exit(f"not a file: {path}")
    sources = typing.cast(dict[str, Source], json.loads(path.read_text()))

    # a site that has not caught up would otherwise recreate a line just moved
    held = {old: now for now, s in sources.items() for old in s.get("was", [])}
    for scan in scans:
        listed = typing.cast(
            dict[str, typing.Any], json.loads(scan.read_text())
        )
        site = typing.cast(str, listed["site"])
        names = typing.cast(list[str], listed["repositories"])
        for name in names:
            source = sources.setdefault(held.get(name, name), {})
            source["via"] = sorted({*source.get("via", []), site})
        log.info("%s: %d repositories", site, len(names))

    write_sources(path, sources)


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [path, *scans] if scans:
            main(pathlib.Path(path), [pathlib.Path(s) for s in scans])
        case _:
            sys.exit("combine.py <sources.json> <scan.json>...")
