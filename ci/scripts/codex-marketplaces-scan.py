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

from agents.nix import configure_logging, discovery, github_token_headers, pool

log = logging.getLogger(__name__)

http = pool(github_token_headers())


def fetch_github_code() -> list[str]:
    # the path qualifier is what separates these from claude's marketplace.json
    return discovery.code(
        http, ['"plugins" filename:marketplace.json path:.agents/plugins']
    )


def fetch_github_topics() -> list[str]:
    return discovery.topics(http, ["codex-plugin", "codex-marketplace"])


FETCHERS = {
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
            sys.exit("codex-marketplaces-scan.py <site> <out.json>")
