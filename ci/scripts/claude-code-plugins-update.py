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


class Snapshot(typing.TypedDict, closed=True):
    rev: str
    version: str
    hash: str
    paths: dict[str, list[str]]
    at: typing.NotRequired[dict[str, Pin]]


KIND = "claude-code-plugins"
SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
CONCURRENCY = 4
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY), SNAPSHOTS
)

PLUGIN_DIR = ".claude-plugin"
MARKETPLACE = f"{PLUGIN_DIR}/marketplace.json"

SEARCH_IGNORE_DIRS = set(
    """
    node_modules .git dist build out target .next .nuxt .cache coverage
    vendor __pycache__ .venv venv .tox .mypy_cache .pytest_cache .gradle
    .idea .bundle .pnpm-store bin obj Pods DerivedData
    """.split()
)
# what Claude Code vendors under a .claude/plugins somebody checked in. These
# are whole copies of other repositories, matched by prefix because `repos` or
# `cache` as a path segment would throw away far more than the cache.
VENDORED = (
    ".claude/plugins/cache/",
    ".claude/plugins/marketplaces/",
    ".claude/plugins/repos/",
)
# past this a repository is mirroring a forge rather than curating plugins; a
# marketplace of a few hundred is ordinary, so `skip: false` can override it
CATALOGUE = 1000


def ignored(directory: str) -> bool:
    return directory.startswith(VENDORED) or not SEARCH_IGNORE_DIRS.isdisjoint(
        directory.split("/")
    )


def wants_marketplace(path: str) -> bool:
    return path == MARKETPLACE or path.endswith(f"/{MARKETPLACE}")


def manifest_roots(files: list[str]) -> list[str]:
    """Every directory holding `.claude-plugin/plugin.json`."""
    found = []
    for path in files:
        head, _, tail = path.rpartition("/")
        root, _, marker = head.rpartition("/")
        if tail == "plugin.json" and marker == PLUGIN_DIR and not ignored(root):
            found.append(root or ".")
    return found


def local_source(entry: object) -> str | None:
    """The path a marketplace entry names, when it names one in here."""
    if isinstance(entry, str):
        source = entry
    elif isinstance(entry, dict):
        listed = typing.cast(dict[str, object], entry)
        # a dict source is `{"source": "github", ...}`, somebody else's plugin
        found = listed.get("source", listed.get("name"))
        source = found if isinstance(found, str) else None
    else:
        source = None
    # `github:owner/repo`, an https url and a scp-style remote all hold a colon
    if source is None or ":" in source or source.startswith("/"):
        return None
    return source


def resolve(base: str, plugin_root: str, source: str) -> str | None:
    parts = [base]
    if "/" not in source and not source.startswith("."):
        # a bare name is looked up under the marketplace's plugin root
        parts.append(plugin_root)
    path = posixpath.normpath(posixpath.join(*parts, source))
    return None if ".." in path.split("/") else path


def marketplace_roots(
    blobs: dict[str, bytes], directories: set[str]
) -> list[str]:
    """Every directory a marketplace manifest in this repository points at.

    A listed plugin need not carry a manifest of its own — the entry names it —
    so this is the only way to find one that ships nothing but skills.
    """
    found = []
    for path, data in sorted(blobs.items()):
        base = posixpath.dirname(posixpath.dirname(path))
        try:
            manifest = json.loads(data)
        except ValueError as error:
            log.warning("skipped %s: %s", path, error)
            continue
        if not isinstance(manifest, dict):
            continue
        listed = typing.cast(dict[str, object], manifest)
        metadata = listed.get("metadata")
        root = (
            typing.cast(dict[str, object], metadata).get("pluginRoot")
            if isinstance(metadata, dict)
            else None
        )
        plugins = listed.get("plugins")
        for entry in plugins if isinstance(plugins, list) else []:
            source = local_source(entry)
            if source is None:
                continue
            resolved = resolve(
                base, root if isinstance(root, str) else "", source
            )
            if resolved is None or ignored(resolved):
                continue
            # "." is the repository root, which no file list spells out
            if resolved == "." or resolved in directories:
                found.append(resolved)
    return found


def directories_in(files: list[str]) -> set[str]:
    found = set()
    for path in files:
        while "/" in path:
            path = path.rpartition("/")[0]
            found.add(path)
    return found


def select_canonical(repo: str, roots: dict[str, bool]) -> list[str]:
    """One path per attribute name, since two would collide in the tree."""

    def rank(path: str) -> tuple[int, int, str]:
        depth = 0 if path == "." else path.count("/") + 1
        # a root that declares itself outranks one a marketplace merely lists
        return 0 if roots[path] else 1, depth, path

    chosen: dict[str, str] = {}
    for path in sorted(roots, key=rank):
        name = repo if path == "." else posixpath.basename(path)
        chosen.setdefault(name.lower(), path)
    return sorted(chosen.values())


def is_mirror(paths: list[str], rule: Source) -> bool:
    if (skip := rule.get("skip")) is not None:
        return bool(skip)
    return len(paths) >= CATALOGUE


def plugins_in(
    repo: str, files: list[str], blobs: dict[str, bytes], rule: Source
) -> list[str]:
    roots = dict.fromkeys(manifest_roots(files), True)
    for path in marketplace_roots(blobs, directories_in(files)):
        roots.setdefault(path, False)
    paths = select_canonical(repo, roots)
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
        digest, files, blobs = engine.fetch_tree(
            owner_repo, candidate.archive_ref, want=wants_marketplace
        )
        paths = plugins_in(owner_repo.split("/")[1], files, blobs, rule)
        globs = {g: p for g, p in extra.items() if p.ref != ref}
        at = {
            path: p.written()
            | {"hash": engine.fetch_tree(owner_repo, p.archive_ref)[0]}
            for path, p in sorted(pin_paths(paths, globs).items())
        }
    except OSError as error:
        log.warning("failed to fetch %s@%s: %s", owner_repo, ref[:7], error)
        return None
    fresh = candidate.written() | {"hash": digest, "paths": group_paths(paths)}
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
            sys.exit("claude-code-plugins-update.py <index/total>")
