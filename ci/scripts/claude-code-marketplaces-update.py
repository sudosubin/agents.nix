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

from agents.nix import (
    Engine,
    Pin,
    Snapshots,
    Source,
    Target,
    configure_logging,
    data_dir,
    flatten_paths,
    github_token_headers,
    pool,
    shard_of,
)

log = logging.getLogger(__name__)


class Snapshot(typing.TypedDict, closed=True):
    rev: str
    version: str
    hash: str
    paths: dict[str, list[str]]
    entries: dict[str, list[str]]
    at: typing.NotRequired[dict[str, Pin]]


KIND = "claude-code-marketplaces"
SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
CONCURRENCY = 4
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY), SNAPSHOTS
)

MANIFEST = ".claude-plugin/marketplace.json"
SEARCH_IGNORE_DIRS = set(
    """
    node_modules .git vendor Pods .bundle .pnpm-store .venv venv
    fixtures _fixtures testdata backups
    """.split()
)
# an installed marketplace is a checkout of the repository it came from
PLUGIN_CACHES = ("/.claude/plugins/", "/.codex/plugins/")
# a git remote names github by its host
GITHUB_URL = re.compile(
    r"(?:(?:https?|ssh|git|git\+ssh)://)?(?:[^@/]+@)?github\.com[:/]"
    r"([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?"
)
# a `github` source names only the repository
GITHUB_REPO = re.compile(r"([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?")


def manifest_dir(path: str) -> str | None:
    """The directory a marketplace is packaged from, `""` at the root."""
    if path == MANIFEST:
        return ""
    if path.endswith(f"/{MANIFEST}"):
        return path[: -len(MANIFEST) - 1]
    return None


def wanted(path: str) -> bool:
    directory = manifest_dir(path)
    return (
        directory is not None
        and SEARCH_IGNORE_DIRS.isdisjoint(directory.split("/"))
        and not any(cache in f"/{directory}/" for cache in PLUGIN_CACHES)
    )


def name_of(manifest: dict[str, typing.Any]) -> str | None:
    name = manifest.get("name")
    return name if isinstance(name, str) and name else None


def local_of(directory: str, root: str, source: str) -> str | None:
    """A relative source as a path from the repository root."""
    if source.startswith("./"):
        path = source[2:]
    elif root:
        # a bare name is resolved against pluginRoot, and only then
        path = posixpath.join(root, source)
    else:
        return None
    joined = posixpath.join(directory, path)
    if joined.startswith("/") or ".." in joined.split("/"):
        return None
    return posixpath.normpath(joined)


def github_of(value: object, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str) or not (found := pattern.fullmatch(value)):
        return None
    return f"github:{found.group(1).lower()}/{found.group(2).lower()}"


def remote_of(source: dict[str, typing.Any]) -> str | None:
    match source.get("source"):
        case "github":
            return github_of(source.get("repo"), GITHUB_REPO)
        case "git-subdir" | "url":
            return github_of(source.get("url"), GITHUB_URL)
        case _:
            # npm, archive and command point at no repository
            return None


def entries_of(
    directory: str, manifest: dict[str, typing.Any]
) -> tuple[set[str], set[str]]:
    metadata = manifest.get("metadata")
    root = metadata.get("pluginRoot") if isinstance(metadata, dict) else None
    local: set[str] = set()
    remote: set[str] = set()
    plugins = manifest.get("plugins")
    for plugin in plugins if isinstance(plugins, list) else []:
        source = plugin.get("source") if isinstance(plugin, dict) else None
        if isinstance(source, str):
            if (path := local_of(directory, root or "", source)) is not None:
                local.add(path)
        elif isinstance(source, dict) and (found := remote_of(source)):
            remote.add(found)
    return local, remote


def marketplaces_in(
    repo: str, blobs: dict[str, bytes], rule: Source
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    found: dict[str, set[str]] = {}
    local: set[str] = set()
    remote: set[str] = set()
    for path, blob in sorted(blobs.items()):
        directory = manifest_dir(path)
        try:
            manifest = json.loads(blob)
        except ValueError as error:
            log.info("skipped %s of %s: %s", path, repo, error)
            continue
        if directory is None or not isinstance(manifest, dict):
            continue
        if not isinstance(manifest.get("plugins"), list):
            continue
        name = name_of(manifest)
        if name is None:
            log.info("skipped %s of %s: no marketplace name", path, repo)
            continue
        found.setdefault(directory, set()).add(name)
        here, there = entries_of(directory, manifest)
        local |= here
        remote |= there

    # more marketplaces than this is a directory of other people's
    most = 8
    skip = rule.get("skip")
    if skip or (skip is None and sum(map(len, found.values())) > most):
        log.info("nothing to package in %s: a mirror", repo)
        return {}, {"local": [], "remote": []}
    if not found:
        log.info("nothing to package in %s: no marketplaces", repo)
    paths = {d: sorted(names) for d, names in sorted(found.items())}
    return paths, {"local": sorted(local), "remote": sorted(remote)}


def update_repo(owner_repo: str, target: Target) -> Snapshot | None:
    candidate, extra, rule = target
    ref = candidate.ref
    log.info("processing %s@%s", owner_repo, candidate.tag or ref[:7])
    try:
        digest, _, blobs = engine.fetch_tree(
            owner_repo, candidate.archive_ref, want=wanted
        )
        paths, entries = marketplaces_in(owner_repo.split("/")[1], blobs, rule)
        at = engine.pins_at(owner_repo, flatten_paths(paths), candidate, extra)
    except OSError as error:
        log.warning("failed to fetch %s@%s: %s", owner_repo, ref[:7], error)
        return None
    fresh = candidate.written() | {
        "hash": digest,
        "paths": paths,
        "entries": entries,
    }
    return typing.cast(Snapshot, fresh | {"at": at} if at else fresh)


def main(shard: str) -> None:
    sources_file = data_dir(KIND) / "sources.json"

    sources = typing.cast(
        dict[str, Source],
        json.loads(sources_file.read_text()) if sources_file.exists() else {},
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
            sys.exit("claude-code-marketplaces-update.py <index/total>")
