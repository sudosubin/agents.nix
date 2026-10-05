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
    Payload,
    configure_logging,
    discovery,
    github_token_headers,
    graphql,
    is_not_found,
    pool,
)

log = logging.getLogger(__name__)

http = pool(github_token_headers())


class Repository(typing.TypedDict):
    marketplace: dict[str, str] | None
    api: dict[str, str] | None


def native(names: list[str]) -> list[str]:
    """Only repositories with a native catalog at their default branch root."""
    accepted: list[str] = []
    ordered = sorted(set(names))
    for start in range(0, len(ordered), 100):
        chunk = ordered[start : start + 100]
        parts = []
        for index, source in enumerate(chunk):
            owner, _, repo = source.removeprefix("github:").partition("/")
            parts.append(f"""
                r{index}: repository(owner: {json.dumps(owner)},
                    name: {json.dumps(repo)}) {{
                    marketplace: object(
                        expression: "HEAD:.agents/plugins/marketplace.json"
                    ) {{ ... on Blob {{ oid }} }}
                    api: object(
                        expression: "HEAD:.agents/plugins/api_marketplace.json"
                    ) {{ ... on Blob {{ oid }} }}
                }}
            """)
        payload: Payload[Repository] = graphql(
            http, "query {" + " ".join(parts) + "}"
        )
        data = payload.get("data")
        if data is None or any(
            error.get("type") != "NOT_FOUND"
            for error in payload.get("errors") or []
        ):
            raise OSError(
                f"could not check marketplace paths: {payload.get('errors')}"
            )
        missing = is_not_found(payload)
        for index, source in enumerate(chunk):
            node = data.get(f"r{index}")
            if node and (node["marketplace"] or node["api"]):
                accepted.append(source)
            elif node is None and f"r{index}" not in missing:
                raise OSError(f"could not check marketplace paths: {source}")
    log.info(
        "%d/%d repositories have native marketplaces",
        len(accepted),
        len(ordered),
    )
    return accepted


def fetch_github_code() -> list[str]:
    # the path qualifier is what separates these from claude's marketplace.json
    return discovery.code(
        http,
        [
            '"plugins" filename:marketplace.json path:.agents/plugins',
            '"plugins" filename:api_marketplace.json path:.agents/plugins',
        ],
    )


def fetch_github_topics() -> list[str]:
    return discovery.topics(http, ["codex-plugin", "codex-marketplace"])


FETCHERS = {
    "github-code": fetch_github_code,
    "github-topics": fetch_github_topics,
}


def main(site: str, out: pathlib.Path) -> None:
    discovery.write_scan(out, site, native(FETCHERS[site]()))


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [site, out] if site in FETCHERS:
            main(site, pathlib.Path(out))
        case _:
            sys.exit("codex-marketplaces-scan.py <site> <out.json>")
