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
    pin_paths,
    pool,
    shard_of,
)

log = logging.getLogger(__name__)

KIND = "kiro-powers"
SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
CONCURRENCY = 4
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY), SNAPSHOTS
)

# Kiro loads a power from either format and says the install is the same, so
# both name a power root. Most of the official registry is still on POWER.md.
MARKERS = frozenset({"POWER.md", "plugin.json"})
# Directories that hold other people's code, where a manifest belongs to
# whoever vendored it rather than to this repository. Build output is not on
# the list: `agent-skills` excludes it because a bare SKILL.md really does turn
# up under dist/, but a power root is named after the power, so excluding
# `build`, `out`, `bin` or `target` would drop powers that go by those names.
# A repository that copies its powers into build output copies them from
# elsewhere in the same tree, and select_canonical keeps one per name anyway.
SEARCH_IGNORE_DIRS = set(
    """
    node_modules .git vendor Pods .bundle .pnpm-store .venv venv
    """.split()
)
# an agent's own plugin directory holds copies it downloaded, not what the
# repository publishes
CACHE_DIRS = (".claude/plugins", ".codex/plugins", ".kiro/plugins")
# the registry itself lists about thirty; past this it is somebody's scrape
CATALOGUE = 200


def is_cached(directory: str) -> bool:
    return any(
        directory == cache or directory.startswith(f"{cache}/")
        for cache in CACHE_DIRS
    )


def find_powers(tree: list[str]) -> list[str]:
    found = set()
    for path in tree:
        directory, _, name = path.rpartition("/")
        if (
            name in MARKERS
            and SEARCH_IGNORE_DIRS.isdisjoint(directory.split("/"))
            and not is_cached(directory)
        ):
            found.add(directory or ".")
    return sorted(found)


def outermost(paths: list[str]) -> list[str]:
    """The powers that are not part of another power.

    A marker below a power root belongs to that power — a template it ships, or
    the skills of a power being migrated — and Kiro would install the root.
    """
    if "." in paths:
        return ["."]
    kept: list[str] = []
    for path in sorted(paths, key=lambda p: (p.count("/"), p)):
        if not any(path.startswith(f"{root}/") for root in kept):
            kept.append(path)
    return sorted(kept)


def select_canonical(repo: str, paths: list[str]) -> list[str]:
    """One power per attribute name, since the name is the directory's."""

    def rank(path: str) -> tuple[int, str]:
        return (0 if path == "." else path.count("/") + 1, path)

    chosen: dict[str, str] = {}
    for path in sorted(paths, key=rank):
        name = repo if path == "." else posixpath.basename(path)
        chosen.setdefault(name.lower(), path)
    return sorted(chosen.values())


def powers_in(repo: str, files: list[str], rule: Source) -> list[str]:
    paths = select_canonical(repo, outermost(find_powers(files)))
    skip = rule.get("skip")
    if skip is not None:
        if skip:
            log.info("nothing to package in %s: %s", repo, skip)
            return []
    elif len(paths) >= CATALOGUE:
        # a repository carrying a real registry's worth of powers is a mirror;
        # `skip: false` in sources.json says otherwise
        log.info("nothing to package in %s: %d powers", repo, len(paths))
        return []
    if not paths:
        log.info("nothing to package in %s: no powers", repo)
    return paths


def update_repo(owner_repo: str, target: Target) -> Snapshot | None:
    candidate, extra, rule = target
    ref = candidate.ref
    log.info("processing %s@%s", owner_repo, candidate.tag or ref[:7])
    try:
        digest, files, _ = engine.fetch_tree(owner_repo, candidate.archive_ref)
        paths = powers_in(owner_repo.split("/")[1], files, rule)
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
            sys.exit("kiro-powers-update.py <index/total>")
