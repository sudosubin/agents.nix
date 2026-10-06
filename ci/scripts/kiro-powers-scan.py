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
from urllib.parse import urlparse

from agents.nix import (
    configure_logging,
    discovery,
    github_token_headers,
    pool,
)

log = logging.getLogger(__name__)

http = pool(github_token_headers())


def fetch_kiro_registry() -> list[str]:
    url = "https://prod.download.desktop.kiro.dev/powers/default_registry.json"
    response = pool().request("GET", url)
    if response.status != 200:
        raise OSError(f"HTTP {response.status} for {url}")
    powers = typing.cast(
        list[dict[str, str]], json.loads(response.data)["powers"]
    )
    repos = []
    for power in powers:
        location = urlparse(power["repositoryUrl"])
        parts = location.path.strip("/").split("/")
        if (
            location.netloc != "github.com"
            or len(parts) < 2
            or not all(parts[:2])
        ):
            raise ValueError(
                f"not a GitHub repository: {power['repositoryUrl']}"
            )
        repos.append(f"github:{parts[0]}/{parts[1].removesuffix('.git')}")
    return repos


def fetch_github_code() -> list[str]:
    # Metadata terms narrow the candidate search.
    return discovery.code(
        http,
        ["name description keywords filename:POWER.md"],
        filenames={"POWER.md"},
    )


def fetch_github_topics() -> list[str]:
    return discovery.topics(http, ["kiro-power"])


FETCHERS = {
    "kiro-registry": fetch_kiro_registry,
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
            sys.exit("kiro-powers-scan.py <site> <out.json>")
