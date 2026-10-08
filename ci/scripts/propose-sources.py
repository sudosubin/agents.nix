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
    Snapshot,
    Snapshots,
    Source,
    blob_at,
    format_sources,
    git,
    propose,
    put_file,
    put_files,
)

TITLES = {
    "update": "chore: update {kind} sources",
    "reconcile": "chore: reconcile {kind} sources with github",
}


def renamed_files(
    path: pathlib.Path, sources: dict[str, Source]
) -> dict[str, bytes | None]:
    snapshots = Snapshots[Snapshot](path.parent, "github.com")
    files: dict[str, bytes | None] = {}
    for name, rule in sources.items():
        if rule.get("skip") or "deleted" in rule:
            continue
        if not name.startswith("github:"):
            continue
        target = snapshots.path(name.removeprefix("github:"))
        for old in rule.get("was", []):
            if not old.startswith("github:") or old == name:
                continue
            previous = snapshots.path(old.removeprefix("github:"))
            for suffix in (".json", ".patch"):
                source = previous.with_suffix(suffix)
                destination = target.with_suffix(suffix)
                if not source.exists():
                    continue
                content = source.read_bytes()
                key = str(destination)
                if key not in files:
                    files[key] = (
                        destination.read_bytes()
                        if destination.exists()
                        else content
                    )
                if suffix == ".patch" and files[key] != content:
                    raise ValueError(
                        f"conflicting patches: {source} and {destination}"
                    )
                files[str(source)] = None
    return files


def main(action: str, path: pathlib.Path) -> None:
    sources = typing.cast(dict[str, Source], json.load(sys.stdin))
    content = format_sources(sources).encode()
    moved = renamed_files(path, sources) if action == "reconcile" else {}
    if content == path.read_bytes() and not moved:
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
                if moved:
                    put_files(moved | {str(path): content}, branch, title, base)
                else:
                    blob = blob_at(base, str(path))
                    put_file(str(path), branch, title, content, blob)
                committed = True
            propose(branch, title, f"## Summary\n\n{NOTE}")
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
