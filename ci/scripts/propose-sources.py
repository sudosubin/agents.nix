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
import subprocess
import sys
import time
import typing

from agents.nix import (
    NOTE,
    Source,
    blob_at,
    format_sources,
    git,
    propose,
    put_file,
)

TITLES = {
    "update": "chore: update {kind} sources",
    "reconcile": "chore: reconcile {kind} sources with github",
}


def main(action: str, path: pathlib.Path) -> None:
    sources = typing.cast(dict[str, Source], json.load(sys.stdin))
    content = format_sources(sources).encode()
    if content == path.read_bytes():
        return

    kind = path.parent.name
    branch = f"sources/{kind}-{action}"
    title = TITLES[action].format(kind=kind)

    base = git("rev-parse", "HEAD").strip()
    prepared = committed = False
    for attempt in range(3):
        try:
            if not prepared:
                git(
                    "push",
                    "-q",
                    "--force",
                    "origin",
                    f"{base}:refs/heads/{branch}",
                )
                prepared = True
            if not committed:
                put_file(
                    str(path), branch, title, content, blob_at(base, str(path))
                )
                committed = True
            propose(branch, title, NOTE)
            return
        except subprocess.CalledProcessError, OSError:
            if attempt == 2:
                raise
            delay = 2 ** (attempt + 1)
            print(
                f"::notice::retrying publication in {delay}s", file=sys.stderr
            )
            time.sleep(delay)


if __name__ == "__main__":
    match sys.argv[1:]:
        case [action, path] if action in TITLES:
            main(action, pathlib.Path(path))
        case _:
            sys.exit(
                "propose-sources.py update|reconcile <sources.json> < new.json"
            )
