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

# the marketplace kind already resolved these, so reading them costs no requests
MARKETPLACES = pathlib.Path("data/copilot-marketplaces/github.com")


def fetch_marketplaces() -> list[str]:
    return discovery.crawl(MARKETPLACES)


def fetch_topics() -> list[str]:
    return discovery.topics(http, ["copilot-plugin"])


FETCHERS = {
    "marketplace-crawl": fetch_marketplaces,
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
            sys.exit("copilot-plugins-scan.py <site> <out.json>")
