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


def main(action: str, path: pathlib.Path) -> int:
    sources = typing.cast(dict[str, Source], json.load(sys.stdin))
    content = format_sources(sources).encode()
    if content == path.read_bytes():
        return 0

    kind = path.parent.name
    branch = f"sources/{kind}-{action}"
    title = TITLES[action].format(kind=kind)

    # git cuts the branch; the api writes the commit, which signs it
    base = git("rev-parse", "HEAD").strip()
    git("push", "-q", "--force", "origin", f"{base}:refs/heads/{branch}")
    try:
        put_file(str(path), branch, title, content, blob_at(base, str(path)))
    except subprocess.CalledProcessError:
        print(
            "::notice::could not write sources; regenerate and retry",
            file=sys.stderr,
        )
        return 75
    propose(branch, title, NOTE)
    return 0


if __name__ == "__main__":
    match sys.argv[1:]:
        case [action, path] if action in TITLES:
            sys.exit(main(action, pathlib.Path(path)))
        case _:
            sys.exit(
                "propose-sources.py update|reconcile <sources.json> < new.json"
            )
