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


KIND = "copilot-marketplaces"
SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
CONCURRENCY = 4
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY), SNAPSHOTS
)

# the places Copilot looks, in the order it looks in them; `.claude-plugin` is
# often a symlink to the canonical one, which is why names are deduplicated
MANIFESTS = (
    "marketplace.json",
    ".plugin/marketplace.json",
    ".github/plugin/marketplace.json",
    ".claude-plugin/marketplace.json",
)
# what the check hook accepts, so nothing is pinned that cannot then be built
NAME = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?")
# a big catalogue is the point of the kind — github/awesome-copilot lists 168
# — so the cap only catches a repository mirroring everything it can find
ENTRY_CAP = 2000


def is_manifest(path: str) -> bool:
    return path in MANIFESTS


def fields_of(value: object) -> dict[str, object]:
    """One JSON object, read as a mapping without trusting its contents."""
    if not isinstance(value, dict):
        return {}
    return typing.cast(dict[str, object], value)


def named(manifest: dict[str, object]) -> str | None:
    name = manifest.get("name")
    if not isinstance(name, str) or not 1 <= len(name) <= 64:
        return None
    if "--" in name or ".." in name or not NAME.fullmatch(name):
        return None
    return name


def plugin_root(manifest: dict[str, object]) -> str:
    root = fields_of(manifest.get("metadata")).get("pluginRoot")
    if not isinstance(root, str):
        return ""
    return root.strip().removeprefix("./").strip("/")


def local_of(source: str, root: str) -> str | None:
    """A relative `source` as a repository-relative directory.

    Copilot accepts `plugins/foo` where Claude Code insists on `./plugins/foo`,
    and resolves a bare name against `metadata.pluginRoot`.
    """
    path = source.strip().removeprefix("./")
    if not path or ":" in path:
        return None
    if "/" not in path and root:
        path = posixpath.join(root, path)
    path = posixpath.normpath(path)
    if path == "." or path.startswith("/") or ".." in path.split("/"):
        return None
    return path


def remote_of(url: str) -> str | None:
    parts = urlparse(url)
    if parts.hostname not in {"github.com", "www.github.com"}:
        return None
    owner, _, tail = parts.path.strip("/").partition("/")
    repo = tail.partition("/")[0].removesuffix(".git")
    if not owner or not repo:
        return None
    return f"github:{owner}/{repo}".lower()


def repo_of(spec: str) -> str | None:
    owner, _, tail = spec.strip().strip("/").partition("/")
    repo = tail.partition("/")[0].removesuffix(".git")
    if not owner or not repo:
        return None
    return f"github:{owner}/{repo}".lower()


def source_of(entry: dict[str, object], root: str) -> tuple[str, str] | None:
    """Where one entry points: `("local", path)` or `("remote", repo)`."""
    source = entry.get("source")
    if isinstance(source, str):
        if "://" in source:
            found = remote_of(source)
            return ("remote", found) if found else None
        local = local_of(source, root)
        return ("local", local) if local else None
    fields = fields_of(source)
    repo = fields.get("repo")
    if fields.get("source") == "github" and isinstance(repo, str):
        found = repo_of(repo)
        return ("remote", found) if found else None
    for key in ("url", "repo"):
        value = fields.get(key)
        if isinstance(value, str) and (found := remote_of(value)):
            return "remote", found
    return None


class Listing(typing.NamedTuple):
    names: list[str]
    local: list[str]
    remote: list[str]
    entries: int


def read_manifests(owner_repo: str, blobs: dict[str, bytes]) -> Listing:
    names: list[str] = []
    local: set[str] = set()
    remote: set[str] = set()
    entries = 0
    for path in MANIFESTS:
        blob = blobs.get(path)
        if blob is None:
            # absent, or a symlink, which the archive reader declines to follow
            continue
        try:
            parsed = json.loads(blob)
        except ValueError as error:
            log.warning("%s: %s is not JSON: %s", owner_repo, path, error)
            continue
        manifest = fields_of(parsed)
        name = named(manifest)
        if name is None:
            log.info("%s: %s names no usable marketplace", owner_repo, path)
            continue
        if name in names:
            continue
        names.append(name)
        plugins = manifest.get("plugins")
        if not isinstance(plugins, list):
            continue
        listed = typing.cast(list[object], plugins)
        entries += len(listed)
        root = plugin_root(manifest)
        for item in listed:
            found = source_of(fields_of(item), root)
            if found is None:
                continue
            where, value = found
            (local if where == "local" else remote).add(value)
    return Listing(sorted(names), sorted(local), sorted(remote), entries)


def is_mirror(entries: int, rule: Source) -> bool:
    if (skip := rule.get("skip")) is not None:
        return bool(skip)
    return entries > ENTRY_CAP


def update_repo(owner_repo: str, target: Target) -> Snapshot | None:
    candidate, _, rule = target
    ref = candidate.ref
    log.info("processing %s@%s", owner_repo, candidate.tag or ref[:7])
    try:
        digest, _, blobs = engine.fetch_tree(
            owner_repo, candidate.archive_ref, want=is_manifest
        )
    except OSError as error:
        log.warning("failed to fetch %s@%s: %s", owner_repo, ref[:7], error)
        return None
    found = read_manifests(owner_repo, blobs)
    if not found.names:
        log.info("nothing to package in %s: no marketplace", owner_repo)
        found = Listing([], [], [], 0)
    elif is_mirror(found.entries, rule):
        log.info("nothing to package in %s: a mirror", owner_repo)
        found = Listing([], [], [], 0)
    fresh = candidate.written() | {
        "hash": digest,
        # the whole repository, because an entry's source is relative to it
        "paths": {"": found.names} if found.names else {},
        "entries": {"local": found.local, "remote": found.remote},
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
            sys.exit("copilot-marketplaces-update.py <index/total>")
