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
    Snapshot,
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

KIND = "copilot-plugins"
SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
CONCURRENCY = 4
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY), SNAPSHOTS
)

# Copilot loads the first of these it finds under a plugin root
MANIFESTS = (
    "plugin.json",
    ".plugin/plugin.json",
    ".github/plugin/plugin.json",
    ".claude-plugin/plugin.json",
)
# and the first of these it finds at a repository root
MARKETPLACES = (
    "marketplace.json",
    ".plugin/marketplace.json",
    ".github/plugin/marketplace.json",
    ".claude-plugin/marketplace.json",
)
# longest first, so a/.plugin/plugin.json is a manifest for a, not for a/.plugin
NESTED = sorted(MANIFESTS, key=len, reverse=True)

SEARCH_IGNORE_DIRS = set(
    """
    node_modules .git vendor Pods .bundle .pnpm-store .venv venv
    fixtures _fixtures testdata backups
    """.split()
)
# a checked-in client cache holds copies of plugins that live elsewhere
CACHE_DIRS = (".claude/plugins/", ".codex/plugins/")
# github/awesome-copilot, the largest marketplace there is, holds 100
CATALOGUE = 500


def ignored(root: str) -> bool:
    parts = root.split("/")
    return not SEARCH_IGNORE_DIRS.isdisjoint(parts) or root.startswith(
        CACHE_DIRS
    )


def root_of(path: str, location: str) -> str | None:
    if path == location:
        return "."
    suffix = f"/{location}"
    return path[: -len(suffix)] if path.endswith(suffix) else None


def carries_manifest(root: str) -> bool:
    """A client's manifest directory, which describes the plugin above it."""
    head, _, tail = root.rpartition("/")
    vendored = tail.startswith(".") and tail.endswith("-plugin")
    return (
        vendored
        or tail == ".plugin"
        or (tail == "plugin" and head.rpartition("/")[2] == ".github")
    )


def declared_roots(files: list[str]) -> set[str]:
    """Every directory holding a plugin manifest."""
    found: set[str] = set()
    for path in files:
        for location in NESTED:
            root = root_of(path, location)
            if root is None:
                continue
            # .codex-plugin and its kind are the plugin above them, not a plugin
            if location == "plugin.json" and carries_manifest(root):
                break
            found.add(root)
            break
    return found


def directories(files: list[str]) -> set[str]:
    found = {"."}
    for path in files:
        parts = path.split("/")[:-1]
        found.update(
            "/".join(parts[:depth]) for depth in range(1, len(parts) + 1)
        )
    return found


def local_sources(manifest: dict[str, typing.Any]) -> list[str]:
    metadata = manifest.get("metadata")
    root = ""
    if isinstance(metadata, dict):
        root = str(metadata.get("pluginRoot") or "").strip("/")
    found: list[str] = []
    for entry in manifest.get("plugins") or []:
        # a dict source names another repository, which this run does not fetch
        if not isinstance(entry, dict) or not isinstance(
            source := entry.get("source"), str
        ):
            continue
        path = source.strip().removeprefix("./").strip("/")
        if not path or "://" in path or ".." in path.split("/"):
            continue
        # a bare name is a directory under the marketplace's plugin root
        found.append(f"{root}/{path}" if root and "/" not in path else path)
    return found


def listed_roots(blobs: dict[str, bytes]) -> list[str]:
    """The plugins a marketplace in this repository names by relative path."""
    for location in MARKETPLACES:
        # an unreadable location is a symlink to another one, so keep walking
        if (data := blobs.get(location)) is None:
            continue
        try:
            manifest = json.loads(data)
        except ValueError as error:
            log.warning("skipped %s: %s", location, error)
            continue
        if isinstance(manifest, dict):
            return local_sources(typing.cast(dict[str, typing.Any], manifest))
    return []


def select_canonical(repo: str, paths: list[str]) -> list[str]:
    def rank(path: str) -> tuple[int, str]:
        return (0 if path == "." else path.count("/") + 1), path

    chosen: dict[str, str] = {}
    for path in sorted(paths, key=rank):
        name = repo if path == "." else posixpath.basename(path)
        chosen.setdefault(name.lower(), path)
    return sorted(chosen.values())


def is_mirror(paths: list[str], rule: Source) -> bool:
    if (skip := rule.get("skip")) is not None:
        return bool(skip)
    return len(paths) >= CATALOGUE


# no outermost(): a root plugin's marketplace lists children that are real too
def plugins_in(
    repo: str, files: list[str], blobs: dict[str, bytes], rule: Source
) -> list[str]:
    here = directories(files)
    declared = declared_roots(files)
    declared.update(path for path in listed_roots(blobs) if path in here)
    roots = [path for path in declared if not ignored(path)]
    paths = select_canonical(repo, roots)
    if not paths:
        log.info("nothing to package in %s: no plugins", repo)
    elif is_mirror(paths, rule):
        log.info("nothing to package in %s: a catalogue", repo)
        return []
    return paths


def update_repo(owner_repo: str, target: Target) -> Snapshot | None:
    candidate, extra, rule = target
    ref = candidate.ref
    log.info("processing %s@%s", owner_repo, candidate.tag or ref[:7])
    try:
        digest, files, blobs = engine.fetch_tree(
            owner_repo,
            candidate.archive_ref,
            want=MARKETPLACES.__contains__,
        )
        paths = plugins_in(owner_repo.split("/")[1], files, blobs, rule)
        at = engine.pins_at(owner_repo, paths, candidate, extra)
    except OSError as error:
        log.warning("failed to fetch %s@%s: %s", owner_repo, ref[:7], error)
        return None
    fresh = candidate.written() | {"hash": digest, "paths": group_paths(paths)}
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
            sys.exit("copilot-plugins-update.py <index/total>")
