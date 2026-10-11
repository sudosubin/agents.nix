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
import pathlib
import posixpath
import sys
import typing

import urllib3
from agents.nix import (
    Source,
    configure_logging,
    data_dir,
    discovery,
    github_token_headers,
    pool,
)
from agents.nix.github import (
    TREE_FIELDS,
    Object,
    objects,
    read_blob,
    root_trees,
)
from agents.nix.snapshots import (
    COPILOT_MANIFESTS as MANIFESTS,
)
from agents.nix.snapshots import (
    copilot_marketplaces_in as marketplaces_in,
)

log = logging.getLogger(__name__)

http = pool(github_token_headers())


def crawl(
    http: urllib3.PoolManager, sources_file: pathlib.Path | None = None
) -> collections.abc.Iterator[str]:
    sources_file = (
        sources_file or data_dir("copilot-marketplaces") / "sources.json"
    )
    if not sources_file.exists():
        log.info("%s does not exist yet", sources_file)
        return
    sources = typing.cast(
        dict[str, Source], json.loads(sources_file.read_text())
    )
    seeds = {
        repo.removeprefix("github:"): rule
        for repo, rule in sorted(sources.items())
        if repo.startswith("github:")
        and "deleted" not in rule
        and not rule.get("skip")
    }
    roots = dict(root_trees(http, seeds))
    trees: dict[tuple[str, str], Object] = {
        (repo, ""): tree for repo, tree in roots.items()
    }
    directories = sorted({posixpath.dirname(path) for path in MANIFESTS} - {""})
    probes = (
        (repo, f"{tree['oid']}:{directory}")
        for repo, tree in roots.items()
        for directory in directories
    )
    for repo, expression, tree in objects(http, probes, TREE_FIELDS):
        if tree is not None and tree.get("kind") == "Tree":
            trees[repo, expression.partition(":")[2]] = tree
    public_http = pool()
    for repo, rule in seeds.items():
        root = roots.get(repo)
        if root is None:
            continue
        blobs: dict[str, bytes] = {}
        for path in MANIFESTS:
            directory, _, name = path.rpartition("/")
            tree = trees.get((repo, directory))
            if tree is None:
                continue
            entry = next(
                (e for e in tree.get("entries") or [] if e["name"] == name),
                None,
            )
            if entry is not None:
                blob = read_blob(public_http, repo, root["rev"], path, entry)
                if blob is not None:
                    blobs[path] = blob
        found = marketplaces_in(repo, blobs, rule)
        if found.local:
            yield f"github:{repo}"
        yield from found.remote


def fetch_marketplace_crawl() -> collections.abc.Iterator[str]:
    return crawl(http)


def fetch_github_topics() -> list[str]:
    return discovery.topics(http, ["copilot-plugin"])


FETCHERS = {
    "marketplace-crawl": fetch_marketplace_crawl,
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
            sys.exit("copilot-plugins-scan.py <site> <out.json>")
