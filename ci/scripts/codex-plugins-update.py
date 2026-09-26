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
    group_paths,
    pin_paths,
    pool,
    shard_of,
)

log = logging.getLogger(__name__)

KIND = "codex-plugins"


class Snapshot(typing.TypedDict, closed=True):
    rev: str
    version: str
    hash: str
    paths: dict[str, list[str]]
    # only the plugins whose manifest name the tree does not already give
    names: typing.NotRequired[dict[str, str]]
    at: typing.NotRequired[dict[str, Pin]]


SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
CONCURRENCY = 4
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY), SNAPSHOTS
)

MANIFEST = "plugin.json"
# .claude-plugin/ is codex's ALTERNATE_PLUGIN_MANIFEST_RELATIVE_PATH
MANIFEST_DIRS = (".codex-plugin", ".claude-plugin")
# vendored third-party code, not build output: a plugin may be called `dist`
SEARCH_IGNORE_DIRS = set(
    """
    node_modules .git vendor Pods .bundle .pnpm-store .venv venv
    """.split()
)
# prefixes, not components: `plugins/` alone is where a repository keeps its own
CACHE_DIRS = (".agents/plugins", ".claude/plugins", ".codex/plugins")
# a repository with hundreds of plugins is a marketplace, which is worth having
CATALOGUE = 500


def manifest_at(root: str, holder: str) -> str:
    inside = f"{holder}/{MANIFEST}"
    return inside if root == "." else f"{root}/{inside}"


# runs over every path in the archive, so it stays cheap
def is_manifest(path: str) -> bool:
    directory, _, name = path.rpartition("/")
    return name == MANIFEST and directory.rpartition("/")[2] in MANIFEST_DIRS


def find_plugins(tree: list[str]) -> list[str]:
    found = set()
    for path in tree:
        directory, _, name = path.rpartition("/")
        root, _, holder = directory.rpartition("/")
        if name != MANIFEST or holder not in MANIFEST_DIRS:
            continue
        if not SEARCH_IGNORE_DIRS.isdisjoint(root.split("/")):
            continue
        if root.startswith(CACHE_DIRS):
            continue
        found.add(root or ".")
    return sorted(found)


# a plugin root inside another one already ships inside its parent
def outermost(paths: list[str]) -> list[str]:
    if "." in paths:
        return ["."]
    return [p for p in paths if not any(p.startswith(f"{o}/") for o in paths)]


# one path per attribute name, the shallowest winning
def select_canonical(repo: str, paths: list[str]) -> list[str]:
    chosen: dict[str, str] = {}
    for path in sorted(paths, key=lambda p: (p.count("/"), p)):
        name = repo if path == "." else posixpath.basename(path)
        chosen.setdefault(name.lower(), path)
    return sorted(chosen.values())


def manifest_blob(path: str, blobs: dict[str, bytes]) -> bytes | None:
    return next(
        (
            found
            for holder in MANIFEST_DIRS
            if (found := blobs.get(manifest_at(path, holder))) is not None
        ),
        None,
    )


# home-manager keys a derivation on pname and never reads its manifest
def names_in(
    repo: str, paths: list[str], blobs: dict[str, bytes]
) -> dict[str, str]:
    named: dict[str, str] = {}
    for path in paths:
        blob = manifest_blob(path, blobs)
        if blob is None:
            continue
        try:
            manifest = json.loads(blob)
        except ValueError:
            # a manifest this broken is worth failing over in the check hook
            continue
        name = manifest.get("name") if isinstance(manifest, dict) else None
        if not isinstance(name, str) or not name:
            continue
        if name != (repo if path == "." else posixpath.basename(path)).lower():
            named[path] = name
    return dict(sorted(named.items()))


def is_mirror(paths: list[str], rule: Source) -> bool:
    if (skip := rule.get("skip")) is not None:
        return bool(skip)
    return len(paths) >= CATALOGUE


def plugins_in(repo: str, files: list[str], rule: Source) -> list[str]:
    paths = select_canonical(repo, outermost(find_plugins(files)))
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
    repo = owner_repo.split("/")[1]
    try:
        # past the cap nothing is packaged, so the budget never has to stretch
        digest, files, blobs = engine.fetch_tree(
            owner_repo, candidate.archive_ref, want=is_manifest, reads=CATALOGUE
        )
        paths = plugins_in(repo, files, rule)
        globs = {g: p for g, p in extra.items() if p.ref != ref}
        at = {
            path: p.written()
            | {"hash": engine.fetch_tree(owner_repo, p.archive_ref)[0]}
            for path, p in sorted(pin_paths(paths, globs).items())
        }
    except OSError as error:
        log.warning("failed to fetch %s@%s: %s", owner_repo, ref[:7], error)
        return None
    fresh: dict[str, typing.Any] = candidate.written() | {
        "hash": digest,
        "paths": group_paths(paths),
    }
    if named := names_in(repo, paths, blobs):
        fresh["names"] = named
    if at:
        fresh["at"] = at
    return typing.cast(Snapshot, fresh)


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
            sys.exit("codex-plugins-update.py <index/total>")
