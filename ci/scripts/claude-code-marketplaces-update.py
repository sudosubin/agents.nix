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
# claude code refuses a marketplace name that holds a space, a control or
# bidirectional-formatting character or a path separator; the attribute it
# becomes is lower cased, so its case is nothing to go by here
UNUSABLE = re.compile(
    r"[\x00-\x20\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069/\\]"
)
# one repository carrying this many marketplaces is a directory of other
# people's, not a marketplace; `skip` in sources.json settles the exceptions
MOST = 8
SEARCH_IGNORE_DIRS = set(
    """
    node_modules .git dist build out target .next .nuxt .cache coverage
    vendor __pycache__ .venv venv .tox .mypy_cache .pytest_cache .gradle
    .idea .bundle .pnpm-store bin obj Pods DerivedData
    """.split()
)
# where an agent unpacks the marketplaces it installed; those are checkouts of
# somebody else's repository, which that repository is already packaged from
PLUGIN_CACHES = ("/.claude/plugins/", "/.codex/plugins/")
# a `url` or `git-subdir` source is a git remote, which only says github by its
# host; a `github` source says it with an `owner/repo` of its own
GITHUB_URL = re.compile(
    r"(?:(?:https?|ssh|git|git\+ssh)://)?(?:[^@/]+@)?(?:github\.com[:/])?"
    r"([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?"
)


def is_ignored(directory: str) -> bool:
    if not SEARCH_IGNORE_DIRS.isdisjoint(directory.split("/")):
        return True
    return any(cache in f"/{directory}/" for cache in PLUGIN_CACHES)


def manifest_dir(path: str) -> str | None:
    """The directory a marketplace is packaged from, `""` at the root."""
    if path == MANIFEST:
        return ""
    if path.endswith(f"/{MANIFEST}"):
        return path[: -len(MANIFEST) - 1]
    return None


def wanted(path: str) -> bool:
    directory = manifest_dir(path)
    return directory is not None and not is_ignored(directory)


def name_of(manifest: dict[str, typing.Any]) -> str | None:
    name = manifest.get("name")
    if not isinstance(name, str) or not name or name == ".":
        return None
    if ".." in name or UNUSABLE.search(name):
        return None
    return name


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


def github_of(url: object) -> str | None:
    if not isinstance(url, str) or not (found := GITHUB_URL.fullmatch(url)):
        return None
    return f"github:{found.group(1).lower()}/{found.group(2).lower()}"


def remote_of(source: dict[str, typing.Any]) -> str | None:
    match source.get("source"):
        case "github":
            return github_of(source.get("repo"))
        case "git-subdir" | "url":
            return github_of(source.get("url"))
        case _:
            # npm, archive and command name no repository to package
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
        name = name_of(manifest)
        if name is None:
            # claude code would refuse it too, so there is nothing to package
            log.info(
                "skipped %s of %s: no name it would load under", path, repo
            )
            continue
        found.setdefault(directory, set()).add(name)
        here, there = entries_of(directory, manifest)
        local |= here
        remote |= there

    skip = rule.get("skip")
    if skip or (skip is None and sum(map(len, found.values())) > MOST):
        log.info("nothing to package in %s: a mirror", repo)
        return {}, {"local": [], "remote": []}
    if not found:
        log.info("nothing to package in %s: no marketplaces", repo)
    paths = {d: sorted(names) for d, names in sorted(found.items())}
    return paths, {"local": sorted(local), "remote": sorted(remote)}


def update_repo(owner_repo: str, target: Target) -> Snapshot | None:
    candidate, _, rule = target
    ref = candidate.ref
    log.info("processing %s@%s", owner_repo, candidate.tag or ref[:7])
    try:
        digest, _, blobs = engine.fetch_tree(
            owner_repo, candidate.archive_ref, want=wanted
        )
    except OSError as error:
        log.warning("failed to fetch %s@%s: %s", owner_repo, ref[:7], error)
        return None
    paths, entries = marketplaces_in(owner_repo.split("/")[1], blobs, rule)
    fresh = candidate.written() | {
        "hash": digest,
        "paths": paths,
        "entries": entries,
    }
    return typing.cast(Snapshot, fresh)


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
        SNAPSHOTS.path(owner_repo).unlink(missing_ok=True)
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
