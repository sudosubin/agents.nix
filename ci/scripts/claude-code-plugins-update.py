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


KIND = "claude-code-plugins"
SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
CONCURRENCY = 4
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY), SNAPSHOTS
)

PLUGIN_DIR = ".claude-plugin"
MANIFEST = "plugin.json"
MARKETPLACE = "marketplace.json"

# other people's code, where a manifest belongs to whoever vendored it
SEARCH_IGNORE_DIRS = set(
    """
    node_modules .git vendor Pods .bundle .pnpm-store .venv venv
    """.split()
)
# matched by prefix because `repos` and `cache` are too generic as components
VENDORED = (
    ".claude/plugins/cache/",
    ".claude/plugins/marketplaces/",
    ".claude/plugins/repos/",
)


def ignored(directory: str) -> bool:
    return directory.startswith(VENDORED) or not SEARCH_IGNORE_DIRS.isdisjoint(
        directory.split("/")
    )


def root_of(path: str, name: str) -> str | None:
    directory, _, file = path.rpartition("/")
    root, _, marker = directory.rpartition("/")
    return root or "." if file == name and marker == PLUGIN_DIR else None


def wants_marketplace(path: str) -> bool:
    return root_of(path, MARKETPLACE) is not None


def manifest_roots(files: list[str]) -> list[str]:
    roots = (root_of(path, MANIFEST) for path in files)
    return [root for root in roots if root and not ignored(root)]


def local_source(entry: object) -> str | None:
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
    # a bare name is looked up under the marketplace's plugin root
    bare = "/" not in source and not source.startswith(".")
    parts = [base, plugin_root, source] if bare else [base, source]
    path = posixpath.normpath(posixpath.join(*parts))
    return None if ".." in path.split("/") else path


def sources_in(manifest: dict[str, object]) -> list[str]:
    plugins = manifest.get("plugins")
    entries = plugins if isinstance(plugins, list) else []
    return [source for entry in entries if (source := local_source(entry))]


def plugin_root_of(manifest: dict[str, object]) -> str:
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    root = typing.cast(dict[str, object], metadata).get("pluginRoot")
    return root if isinstance(root, str) else ""


def marketplace_roots(
    blobs: dict[str, bytes], directories: set[str]
) -> list[str]:
    found = []
    for path, data in sorted(blobs.items()):
        try:
            manifest = json.loads(data)
        except ValueError as error:
            log.warning("skipped %s: %s", path, error)
            continue
        if not isinstance(manifest, dict):
            continue
        listed = typing.cast(dict[str, object], manifest)
        base = root_of(path, MARKETPLACE) or "."
        for source in sources_in(listed):
            resolved = resolve(base, plugin_root_of(listed), source)
            if resolved is None or ignored(resolved):
                continue
            # "." is the repository root, which no file list spells out
            if resolved == "." or resolved in directories:
                found.append(resolved)
    return found


def directories(files: list[str]) -> set[str]:
    return {
        directory
        for path in files
        for directory in itertools.accumulate(
            path.split("/")[:-1], posixpath.join
        )
    }


def select_canonical(repo: str, roots: dict[str, bool]) -> list[str]:
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
    # past this a repository is mirroring a forge rather than curating plugins
    catalogue = 1000
    if (skip := rule.get("skip")) is not None:
        return bool(skip)
    return len(paths) >= catalogue


# no outermost(): a root plugin's marketplace lists children that are real too
def plugins_in(
    repo: str, files: list[str], blobs: dict[str, bytes], rule: Source
) -> list[str]:
    roots = dict.fromkeys(manifest_roots(files), True)
    for path in marketplace_roots(blobs, directories(files)):
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
        at = engine.pins_at(owner_repo, paths, candidate, extra)
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
