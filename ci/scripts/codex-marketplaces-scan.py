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

import pathlib
import sys

from agents.nix import (
    configure_logging,
    discovery,
    github_token_headers,
    pool,
)

http = pool(github_token_headers())


def fetch_github_code() -> list[str]:
    return discovery.code(
        http,
        [
            '"plugins" filename:marketplace.json path:.agents/plugins',
            '"plugins" filename:api_marketplace.json path:.agents/plugins',
        ],
        paths={
            ".agents/plugins/marketplace.json",
            ".agents/plugins/api_marketplace.json",
        },
    )


def main(out: pathlib.Path) -> None:
    discovery.write_scan(out, "github-code", fetch_github_code())


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case ["github-code", out]:
            main(pathlib.Path(out))
        case _:
            sys.exit("codex-marketplaces-scan.py github-code <out.json>")
