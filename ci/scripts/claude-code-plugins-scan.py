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

import collections.abc
import json
import logging
import operator
import pathlib
import posixpath
import re
import sys
import typing
from concurrent.futures import ThreadPoolExecutor

import urllib3
from agents.nix import (
    configure_logging,
    data_dir,
    discovery,
    github_token_headers,
    pool,
    snapshots,
)
from agents.nix.github import (
    read_blob,
    root_trees,
    tree_entries,
)
from agents.nix.sources import marketplace_sources

log = logging.getLogger(__name__)

# claude.com/robots.txt is `Allow: /` with nothing disallowed; 2 is courtesy
CONCURRENCY = 2
http = pool(maxsize=CONCURRENCY)
github = pool(github_token_headers(), maxsize=4)

DIRECTORY = "https://claude.com/marketplace/plugins"
# the whole directory arrives as one page, streamed as Next.js flight chunks
FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\]\)')
SLUG = re.compile(r'"slug":"([^"]+)"')
REPO = re.compile(r"https://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)")


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


def crawl(
    http: urllib3.PoolManager, sources_file: pathlib.Path | None = None
) -> collections.abc.Iterator[str]:
    seeds = marketplace_sources(
        sources_file or data_dir("claude-code-marketplaces") / "sources.json"
    )
    roots = dict(root_trees(http, seeds))
    public_http = pool(maxsize=4)

    def read_catalogs(repo: str) -> tuple[str, dict[str, bytes]]:
        found: dict[str, bytes] = {}
        pending = [("", roots[repo]["oid"])]
        while pending:
            directory, oid = pending.pop()
            entries, truncated = tree_entries(http, repo, oid)
            if truncated:
                # A complete immediate list lets us split an oversized tree.
                entries, truncated = tree_entries(http, repo, oid, False)
                if truncated:
                    raise OSError(
                        f"Git directory list truncated: {repo}:{directory}"
                    )
                pending.extend(
                    (posixpath.join(directory, entry["name"]), entry["oid"])
                    for entry in reversed(entries)
                    if entry["type"] == "tree"
                    and snapshots.claude_wanted(
                        posixpath.join(
                            directory, entry["name"], snapshots.CLAUDE_MANIFEST
                        )
                    )
                )
            for entry in sorted(entries, key=operator.itemgetter("name")):
                path = posixpath.join(directory, entry["name"])
                if (
                    snapshots.claude_wanted(path)
                    and (
                        blob := read_blob(
                            public_http, repo, roots[repo]["rev"], path, entry
                        )
                    )
                    is not None
                ):
                    found[path] = blob
                    if seeds[repo].get("skip") is None:
                        paths, _ = snapshots.claude_marketplaces_in(
                            repo, found, {"skip": False}
                        )
                        if sum(map(len, paths.values())) > 8:
                            log.info("nothing to package in %s: a mirror", repo)
                            return repo, {}
        return repo, found

    with ThreadPoolExecutor(max_workers=4) as workers:
        for repo, catalogs in workers.map(read_catalogs, roots, buffersize=25):
            _, entries = snapshots.claude_marketplaces_in(
                repo, catalogs, seeds[repo]
            )
            if entries["local"]:
                yield f"github:{repo}"
            yield from entries["remote"]


def fetch_marketplace_crawl() -> collections.abc.Iterator[str]:
    return crawl(github)


def fetch_github_topics() -> list[str]:
    return discovery.topics(
        github, ["claude-code-plugin", "claude-code-plugins"]
    )


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
