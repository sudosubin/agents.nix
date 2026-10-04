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
import re
import sys
import typing
from concurrent.futures import ThreadPoolExecutor

from agents.nix import (
    configure_logging,
    discovery,
    github_token_headers,
    pool,
)

log = logging.getLogger(__name__)

# claude.com/robots.txt is `Allow: /` with nothing disallowed; 2 is courtesy
CONCURRENCY = 2
http = pool(maxsize=CONCURRENCY)

DIRECTORY = "https://claude.com/marketplace/plugins"
# the whole directory arrives as one page, streamed as Next.js flight chunks
FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\]\)')
SLUG = re.compile(r'"slug":"([^"]+)"')
REPO = re.compile(r"https://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)")


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


def get(url: str) -> str:
    response = http.request("GET", url)
    if response.status != 200:
        raise OSError(f"HTTP {response.status} for {url}")
    return response.data.decode("utf-8", "replace")


def flight(html: str) -> str:
    return "".join(
        typing.cast(str, json.loads(chunk)) for chunk in FLIGHT.findall(html)
    )


def repo_of(slug: str) -> str | None:
    try:
        page = get(f"{DIRECTORY}/{slug}")
    except OSError as error:
        log.warning("skipped %s: %s", slug, error)
        return None
    found = REPO.search(page)
    if found is None:
        # a plugin can ship from somewhere we cannot fetch, an npm package say
        log.info("  %s names no repository", slug)
        return None
    owner, repo = found.groups()
    return f"github:{owner}/{repo.removesuffix('.git')}"


def fetch_claude_com() -> list[str]:
    slugs = sorted(set(SLUG.findall(flight(get(DIRECTORY)))))
    log.info("claude.com: %d plugins", len(slugs))
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as workers:
        return [repo for repo in workers.map(repo_of, slugs) if repo]


def fetch_marketplace_crawl() -> list[str]:
    return crawl(pathlib.Path("data/claude-code-marketplaces/github.com"))


def fetch_github_topics() -> list[str]:
    search = pool(github_token_headers())
    names = ["claude-code-plugin", "claude-code-plugins"]
    return discovery.topics(search, names)


FETCHERS = {
    "claude-com": fetch_claude_com,
    "github-topics": fetch_github_topics,
    "marketplace-crawl": fetch_marketplace_crawl,
}


def main(site: str, out: pathlib.Path) -> None:
    discovery.write_scan(out, site, FETCHERS[site]())


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [site, out] if site in FETCHERS:
            main(site, pathlib.Path(out))
        case _:
            sys.exit("claude-code-plugins-scan.py <site> <out.json>")
