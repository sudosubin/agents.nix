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
import posixpath
import re
import sys
import typing
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from agents.nix import (
    Engine,
    Pin,
    Snapshots,
    Source,
    Target,
    configure_logging,
    data_dir,
    github_token_headers,
    group_paths,
    pool,
    shard_of,
)

log = logging.getLogger(__name__)

type Manifest = dict[str, typing.Any]


class Snapshot(typing.TypedDict, closed=True):
    rev: str
    version: str
    hash: str
    paths: dict[str, list[str]]
    entries: dict[str, list[str]]
    at: typing.NotRequired[dict[str, Pin]]


KIND = "codex-marketplaces"
SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
CONCURRENCY = 4
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY), SNAPSHOTS
)

# Native Codex catalogs; compatibility catalogs belong to their own kinds.
MANIFESTS = (
    ".agents/plugins/marketplace.json",
    ".agents/plugins/api_marketplace.json",
)
REMOTE = frozenset({"git-subdir", "github", "url"})


def manifests_in(
    owner_repo: str, files: list[str], blobs: dict[str, bytes]
) -> list[tuple[str, Manifest]]:
    for manifest in MANIFESTS:
        if manifest not in files:
            continue
        blob = blobs.get(manifest)
        if blob is None:
            log.info("skipped %s/%s: unreadable manifest", owner_repo, manifest)
            return []
        try:
            document = json.loads(blob)
        except ValueError as error:
            log.info("skipped %s/%s: %s", owner_repo, manifest, error)
            return []
        return (
            [(manifest, typing.cast(Manifest, document))]
            if isinstance(document, dict)
            else []
        )
    return []


def named(document: Manifest) -> str | None:
    """A manifest's own name, used as the package attribute."""
    name = document.get("name")
    return name if isinstance(name, str) and name else None


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


def github_repo(url: str) -> str | None:
    # git@github.com:owner/repo.git is a url everywhere but to urlparse
    scp = re.fullmatch(r"[\w.+-]+@([\w.-]+):(.+)", url)
    parsed = urlparse(f"https://{scp[1]}/{scp[2]}" if scp else url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return None
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if len(parts) < 2 or not all(parts[:2]):
        return None
    return f"github:{parts[0].lower()}/{parts[1].lower()}"


def remote_of(source: Manifest) -> str | None:
    """A remote source as `github:owner/repo`, where it is one."""
    if source.get("source") not in REMOTE:
        return None
    url = source.get("url")
    if isinstance(url, str):
        return github_repo(url)
    # no manifest ships a `github` source to copy, so only the obvious spelling
    repo = source.get("repo")
    if isinstance(repo, str) and re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return f"github:{repo.lower()}"
    return None


def directories(files: list[str]) -> set[str]:
    """Every directory the archive holds, which lists only its files."""
    dirs: set[str] = set()
    for file in files:
        path = posixpath.dirname(file)
        while path and path not in dirs:
            dirs.add(path)
            path = posixpath.dirname(path)
    return dirs


def marketplaces_in(
    owner_repo: str, files: list[str], blobs: dict[str, bytes], rule: Source
) -> tuple[list[str], dict[str, list[str]]]:
    """The marketplace names a revision holds, and what they point at."""
    dirs = directories(files)
    names: dict[str, str] = {}
    local: set[str] = set()
    remote: set[str] = set()
    counted = 0
    for manifest, document in manifests_in(owner_repo, files, blobs):
        name = named(document)
        if name is None:
            log.info("skipped %s/%s: no marketplace name", owner_repo, manifest)
            continue
        sources = sources_of(document)
        here = {p for source in sources if (p := local_of(source))}
        # A listed path may be a directory symlink; the hook checks its type.
        if missing := sorted(here - dirs - set(files)):
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
    elif is_mirror(counted, rule):
        log.info("nothing to package in %s: an index", owner_repo)
        return [], {"local": [], "remote": []}
    return sorted(names.values()), {
        "local": sorted(local),
        "remote": sorted(remote),
    }


def is_mirror(entries: int, rule: Source) -> bool:
    # past this it is an index of everything findable, not a curated marketplace
    entry_cap = 2000
    if (skip := rule.get("skip")) is not None:
        return bool(skip)
    return entries > entry_cap


def update_repo(owner_repo: str, target: Target) -> Snapshot | None:
    candidate, extra, rule = target
    ref = candidate.ref
    log.info("processing %s@%s", owner_repo, candidate.tag or ref[:7])
    try:
        digest, files, blobs = engine.fetch_tree(
            owner_repo, candidate.archive_ref, want=MANIFESTS.__contains__
        )
        names, entries = marketplaces_in(owner_repo, files, blobs, rule)
        at = engine.pins_at(owner_repo, names, candidate, extra)
    except OSError as error:
        log.warning("failed to fetch %s@%s: %s", owner_repo, ref[:7], error)
        return None
    fresh = candidate.written() | {
        "hash": digest,
        "paths": group_paths(names),
        "entries": entries,
    }
    return typing.cast(Snapshot, fresh | {"at": at} if at else fresh)


def main(shard: str) -> None:
    sources_dir = data_dir(KIND) / "sources.json"

    sources = typing.cast(
        dict[str, Source],
        json.loads(sources_dir.read_text()) if sources_dir.exists() else {},
    )

    mine, retired = shard_of(sources, shard)
    stale = [source.removeprefix("github:") for source in retired]
    known = {
        s: e for s in mine if (e := SNAPSHOTS.read(s.removeprefix("github:")))
    }
    log.info(
        "shard %s: %d repos, %d known, %d retired",
        shard,
        len(mine),
        len(known),
        len(retired),
    )

    targets, relabel, missing = engine.plan(mine, known, sources)
    gone = engine.drop(missing, len(mine))
    log.info("%d to fetch, %d relabelled", len(targets), len(relabel))
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as workers:
        snapshots = list(workers.map(update_repo, targets, targets.values()))

    for owner_repo in stale + gone:
        SNAPSHOTS.remove(owner_repo)
    for owner_repo, snapshot in relabel.items():
        SNAPSHOTS.write(owner_repo, snapshot)
    written = 0
    for owner_repo, snapshot in zip(targets, snapshots, strict=True):
        if snapshot:
            SNAPSHOTS.write(owner_repo, snapshot)
            written += 1
    log.info("wrote %d snapshots", written)


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [shard] if re.fullmatch(r"[1-9]\d*/[1-9]\d*", shard):
            main(shard)
        case _:
            sys.exit("codex-marketplaces-update.py <index/total>")
