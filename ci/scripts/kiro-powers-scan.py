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

import logging
import pathlib
import sys

from agents.nix import (
    configure_logging,
    discovery,
    github_token_headers,
    pool,
    write_scan,
)

log = logging.getLogger(__name__)

# kiro.dev/powers is a website with no registry endpoint, so GitHub is the index
http = pool(github_token_headers())


def fetch_github_code() -> list[str]:
    # a filename: qualifier needs a term beside it, and every power has steering
    return discovery.code(http, ['"steering" filename:POWER.md'])


def fetch_github_topics() -> list[str]:
    return discovery.topics(http, ["kiro-power"])


FETCHERS = {
    "github-code": fetch_github_code,
    "github-topics": fetch_github_topics,
}


def main(site: str, out: pathlib.Path) -> None:
    write_scan(out, site, FETCHERS[site]())


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [site, out] if site in FETCHERS:
            main(site, pathlib.Path(out))
        case _:
            sys.exit("kiro-powers-scan.py <site> <out.json>")
