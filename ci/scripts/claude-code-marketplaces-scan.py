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

"""The sites that list Claude Code marketplaces.

claudemarketplaces.com disallows `/api/` in its robots.txt, the same as the
skillsdirectory.com registry `agent-skills-scan.py` already reads; whether this
project honours that is an open decision for the maintainer.
"""

import json
import logging
import pathlib
import sys
import typing

from agents.nix import (
    configure_logging,
    discovery,
    github_token_headers,
    pool,
)

log = logging.getLogger(__name__)

http = pool()


def fetch_claudemarketplaces() -> list[str]:
    url = "https://claudemarketplaces.com/api/marketplaces"
    response = http.request("GET", url)
    if response.status != 200:
        raise OSError(f"HTTP {response.status} for {url}")
    listed = typing.cast(list[dict[str, typing.Any]], json.loads(response.data))
    log.info("claudemarketplaces.com: %d marketplaces", len(listed))
    return [f"github:{repo}" for m in listed if (repo := m.get("repo"))]


def fetch_github_code() -> list[str]:
    # the manifest's own marker: no other file of that name carries the key
    query = '"claude-plugin" filename:marketplace.json'
    return discovery.code(pool(github_token_headers()), [query])


def fetch_github_topics() -> list[str]:
    return discovery.topics(
        pool(github_token_headers()),
        ["claude-code-marketplace", "claude-code-plugins"],
    )


FETCHERS = {
    "claudemarketplaces-com": fetch_claudemarketplaces,
    "github-code": fetch_github_code,
    "github-topics": fetch_github_topics,
}


def main(site: str, out: pathlib.Path) -> None:
    discovery.write_scan(out, site, FETCHERS[site]())


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [site, out] if site in FETCHERS:
            main(site, pathlib.Path(out))
        case _:
            sys.exit("claude-code-marketplaces-scan.py <site> <out.json>")
