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

import itertools
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
    group_paths,
    pool,
    shard_of,
)

log = logging.getLogger(__name__)


class Snapshot(typing.TypedDict, closed=True):
    rev: str
    version: str
    hash: str
    paths: dict[str, list[str]]
    at: typing.NotRequired[dict[str, Pin]]


KIND = "agent-plugins"
SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
CONCURRENCY = 4
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY), SNAPSHOTS
)

MANIFEST = "plugin.json"
# the marker for the kind: the other plugin formats keep a plugin.json too
SCHEMA_PREFIX = "https://agent-plugins.org/schemas/"
# far over what agent-skills allows, because a catalogue is the point here
CATALOGUE = 1000
SEARCH_IGNORE_DIRS = set(
    """
    node_modules .git vendor Pods .bundle .pnpm-store .venv venv
    fixtures _fixtures testdata backups
    """.split()
)


def is_manifest(path: str) -> bool:
    directory, _, name = path.rpartition("/")
    parts = directory.split("/")
    # a client that installed plugins checks its cache in as `.<tool>/plugins`
    cached = any(
        head.startswith(".") and tail == "plugins"
        for head, tail in itertools.pairwise(parts)
    )
    return (
        name == MANIFEST and SEARCH_IGNORE_DIRS.isdisjoint(parts) and not cached
    )


def plugin_at(path: str, blob: bytes) -> str | None:
    try:
        manifest = json.loads(blob)
    except ValueError:
        return None
    marker = manifest.get("$schema") if isinstance(manifest, dict) else None
    identified = (
        isinstance(marker, str)
        and marker.startswith(SCHEMA_PREFIX)
        and marker.endswith("/plugin.schema.json")
    )
    return posixpath.dirname(path) or "." if identified else None


# a manifest under a plugin is a client extension directory, not a plugin
def outermost(paths: list[str]) -> list[str]:
    roots = set(paths)

    def nested(path: str) -> bool:
        parts = path.split("/")
        heads = ("/".join(parts[:n]) for n in range(1, len(parts)))
        return (path != "." and "." in roots) or any(h in roots for h in heads)

    return sorted(path for path in paths if not nested(path))


# two plugins of one name would collide in the attribute set
def select_canonical(repo: str, paths: list[str]) -> list[str]:
    def rank(path: str) -> tuple[int, str]:
        return 0 if path == "." else path.count("/") + 1, path

    chosen: dict[str, str] = {}
    for path in sorted(paths, key=rank):
        name = repo if path == "." else posixpath.basename(path)
        chosen.setdefault(name.lower(), path)
    return sorted(chosen.values())


def is_mirror(paths: list[str], rule: Source) -> bool:
    if (skip := rule.get("skip")) is not None:
        return bool(skip)
    return len(paths) > CATALOGUE


def plugins_in(repo: str, blobs: dict[str, bytes], rule: Source) -> list[str]:
    found = [p for path, blob in blobs.items() if (p := plugin_at(path, blob))]
    paths = select_canonical(repo, outermost(found))
    if not paths:
        log.info("nothing to package in %s: no plugins", repo)
    elif is_mirror(paths, rule):
        log.info("nothing to package in %s: a mirror", repo)
        return []
    return paths


def update_repo(owner_repo: str, target: Target) -> Snapshot | None:
    candidate, extra, rule = target
    ref = candidate.ref
    log.info("processing %s@%s", owner_repo, candidate.tag or ref[:7])
    try:
        digest, _, blobs = engine.fetch_tree(
            owner_repo,
            candidate.archive_ref,
            want=is_manifest,
            # one past the cap, so a repository over it still reads as over it
            reads=CATALOGUE + 1,
        )
        paths = plugins_in(owner_repo.split("/")[1], blobs, rule)
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
            sys.exit("agent-plugins-update.py <index/total>")
