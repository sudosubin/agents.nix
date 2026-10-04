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
import re
import sys
import typing

from agents.nix import (
    Engine,
    Packaged,
    Snapshot,
    Snapshots,
    Source,
    configure_logging,
    data_dir,
    directories,
    github_token_headers,
    is_mirror,
    pool,
    repo_at,
    repo_named,
)

log = logging.getLogger(__name__)

type Manifest = dict[str, typing.Any]


class Listing(Snapshot):
    """A marketplace snapshot also records what its entries point at."""

    entries: dict[str, list[str]]


KIND = "codex-marketplaces"
CONCURRENCY = 4
SNAPSHOTS: Snapshots[Listing] = Snapshots(data_dir(KIND), "github.com")
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY),
    SNAPSHOTS,
    CONCURRENCY,
)

# codex-rs/core-plugins/src/marketplace.rs, MARKETPLACE_MANIFEST_RELATIVE_PATHS
MANIFESTS = (
    ".agents/plugins/marketplace.json",
    ".agents/plugins/api_marketplace.json",
    ".claude-plugin/marketplace.json",
    ".cursor-plugin/marketplace.json",
)
REMOTE = frozenset({"git-subdir", "github", "url"})
# identifier_validation.py's validate_marketplace_name, in codex-rs/skills
NAME = re.compile(r"[A-Za-z0-9_-]+")
# past this it is an index of everything findable, not a curated marketplace
ENTRY_CAP = 2000


def manifests_in(
    owner_repo: str, blobs: dict[str, bytes]
) -> list[tuple[str, Manifest]]:
    found = []
    for manifest in MANIFESTS:
        blob = blobs.get(manifest)
        if blob is None:
            continue
        try:
            document = json.loads(blob)
        except ValueError as error:
            log.info("skipped %s/%s: %s", owner_repo, manifest, error)
            continue
        if isinstance(document, dict):
            found.append((manifest, typing.cast(Manifest, document)))
    return found


def named(document: Manifest) -> str | None:
    """A manifest's own name, when it makes a usable attribute."""
    name = document.get("name")
    if not isinstance(name, str) or len(name) > 64:
        return None
    return name if NAME.fullmatch(name) else None


def sources_of(document: Manifest) -> list[Manifest]:
    plugins = document.get("plugins")
    if not isinstance(plugins, list):
        return []
    return [
        entry["source"]
        for entry in typing.cast(list[typing.Any], plugins)
        if isinstance(entry, dict) and isinstance(entry.get("source"), dict)
    ]


def local_of(source: Manifest) -> str | None:
    """A local source as a repository-relative directory."""
    if source.get("source") != "local":
        return None
    path = source.get("path")
    if not isinstance(path, str) or path.startswith("/"):
        return None
    split = typing.cast(list[str], path.split("/"))
    parts = [part for part in split if part not in {"", "."}]
    return None if not parts or ".." in parts else "/".join(parts)


def remote_of(source: Manifest) -> str | None:
    """A remote source as `github:owner/repo`, where it is one."""
    if source.get("source") not in REMOTE:
        return None
    url = source.get("url")
    if isinstance(url, str):
        return repo_at(url)
    # no manifest ships a `github` source to copy, so only the obvious spelling
    repo = source.get("repo")
    return repo_named(repo) if isinstance(repo, str) else None


def is_manifest(path: str) -> bool:
    return path in MANIFESTS


def marketplaces_in(
    owner_repo: str, files: list[str], blobs: dict[str, bytes], rule: Source
) -> Packaged:
    """The marketplace names a revision holds, and what they point at."""
    dirs = directories(files)
    names: dict[str, str] = {}
    local: set[str] = set()
    remote: set[str] = set()
    counted = 0
    for manifest, document in manifests_in(owner_repo, blobs):
        name = named(document)
        if name is None:
            log.info("skipped %s/%s: unusable name", owner_repo, manifest)
            continue
        sources = sources_of(document)
        here = {p for source in sources if (p := local_of(source))}
        # the check hook refuses these, so an attribute could only fail to build
        if missing := sorted(here - dirs):
            log.info(
                "skipped %s/%s: %d paths missing, first %s",
                owner_repo,
                manifest,
                len(missing),
                missing[0],
            )
            continue
        counted += len(sources)
        names.setdefault(name.lower(), name)
        local |= here
        remote |= {r for source in sources if (r := remote_of(source))}

    if not names:
        log.info("nothing to package in %s: no marketplace", owner_repo)
    elif is_mirror(rule, counted > ENTRY_CAP):
        log.info("nothing to package in %s: an index", owner_repo)
        return [], {"entries": {"local": [], "remote": []}}
    entries = {"local": sorted(local), "remote": sorted(remote)}
    return sorted(names.values()), {"entries": entries}


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [shard] if re.fullmatch(r"[1-9]\d*/[1-9]\d*", shard):
            engine.run(shard, marketplaces_in, want=is_manifest)
        case _:
            sys.exit("codex-marketplaces-update.py <index/total>")
