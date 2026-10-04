#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.15"
# dependencies = ["agents.nix", "urllib3>=2.5"]
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
    configure_logging,
    data_dir,
    discovery,
    github_token_headers,
    pool,
)

log = logging.getLogger(__name__)

http = pool(github_token_headers())


# read off disk, so it costs nothing and is empty until that kind has written
def fetch_marketplace_crawl() -> list[str]:
    return crawl(data_dir("codex-marketplaces") / "github.com")


def crawl(directory: pathlib.Path) -> list[str]:
    """The repositories the marketplace kind's snapshots point at."""
    repos: list[str] = []
    if not directory.is_dir():
        log.info("%s does not exist yet", directory)
        return repos
    for file in sorted(directory.glob("*/*.json")):
        try:
            snapshot = typing.cast(
                dict[str, typing.Any], json.loads(file.read_text())
            )
        except ValueError as error:
            log.warning("skipped %s: %s", file, error)
            continue
        entries = typing.cast(
            dict[str, list[str]], snapshot.get("entries") or {}
        )
        if entries.get("local"):
            # a relative source names the marketplace repository itself
            repos.append(f"github:{file.parent.name}/{file.stem}")
        repos += entries.get("remote") or []
    return repos


def fetch_code() -> list[str]:
    return discovery.code(http, ['".codex-plugin" filename:plugin.json'])


def fetch_topics() -> list[str]:
    return discovery.topics(http, ["codex-plugin"])


FETCHERS = {
    "marketplace-crawl": fetch_marketplace_crawl,
    "github-code": fetch_code,
    "github-topics": fetch_topics,
}


def main(site: str, out: pathlib.Path) -> None:
    discovery.write_scan(out, site, FETCHERS[site]())


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [site, out] if site in FETCHERS:
            main(site, pathlib.Path(out))
        case _:
            sys.exit("codex-plugins-scan.py <site> <out.json>")
