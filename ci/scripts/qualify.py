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

from agents.nix import (
    Payload,
    Source,
    configure_logging,
    github_token_headers,
    graphql,
    is_not_found,
    pool,
)

log = logging.getLogger(__name__)


class Repository(typing.TypedDict):
    stargazerCount: int


def popular(names: set[str]) -> set[str]:
    http = pool(github_token_headers())
    accepted: set[str] = set()
    ordered = sorted(names)
    for start in range(0, len(ordered), 100):
        chunk = ordered[start : start + 100]
        parts = []
        for index, source in enumerate(chunk):
            owner, _, repo = source.removeprefix("github:").partition("/")
            parts.append(
                f"r{index}: repository(owner: {json.dumps(owner)}, "
                f"name: {json.dumps(repo)}) {{ stargazerCount }}"
            )
        payload: Payload[Repository] = graphql(
            http, "query {" + " ".join(parts) + "}"
        )
        data = payload.get("data")
        if data is None:
            raise OSError(
                f"could not check repository stars: {payload.get('errors')}"
            )
        missing = is_not_found(payload)
        for index, source in enumerate(chunk):
            node = data.get(f"r{index}")
            if node and node["stargazerCount"] >= 10:
                accepted.add(source)
            elif node is None and f"r{index}" not in missing:
                raise OSError(f"could not check repository stars: {source}")
    log.info(
        "%d/%d new repositories have at least 10 stars",
        len(accepted),
        len(names),
    )
    return accepted


def main(path: pathlib.Path, scans: list[pathlib.Path]) -> None:
    if not path.is_file():
        sys.exit(f"not a file: {path}")
    sources = typing.cast(dict[str, Source], json.loads(path.read_text()))

    # a site that has not caught up would otherwise recreate a line just moved
    held = {old: now for now, s in sources.items() for old in s.get("was", [])}
    listed_by: dict[str, set[str]] = {}
    for scan in scans:
        listed = typing.cast(
            dict[str, typing.Any], json.loads(scan.read_text())
        )
        site = typing.cast(str, listed["site"])
        names = typing.cast(list[str], listed["repositories"])
        for name in names:
            listed_by.setdefault(held.get(name, name), set()).add(site)
        log.info("%s: %d repositories", site, len(names))

    accepted = popular(listed_by.keys() - sources.keys())
    print(
        json.dumps({
            name: sorted(sites)
            for name, sites in listed_by.items()
            if name in sources or name in accepted
        })
    )


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [path, *scans] if scans:
            main(pathlib.Path(path), [pathlib.Path(s) for s in scans])
        case _:
            sys.exit("qualify.py <sources.json> <scan.json>...")
