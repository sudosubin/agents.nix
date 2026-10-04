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

from agents.nix import (
    Engine,
    Packaged,
    Snapshot,
    Snapshots,
    Source,
    configure_logging,
    data_dir,
    github_token_headers,
    is_mirror,
    pool,
    repo_at,
    repo_named,
)

log = logging.getLogger(__name__)


class Listing(Snapshot):
    """A marketplace snapshot also records what its entries point at."""

    entries: dict[str, list[str]]


KIND = "copilot-marketplaces"
CONCURRENCY = 4
SNAPSHOTS: Snapshots[Listing] = Snapshots(data_dir(KIND), "github.com")
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY),
    SNAPSHOTS,
    CONCURRENCY,
)

# the order Copilot looks in, so the first manifest under a name wins
MANIFESTS = (
    "marketplace.json",
    ".plugin/marketplace.json",
    ".github/plugin/marketplace.json",
    ".claude-plugin/marketplace.json",
)
# what the check hook accepts, so nothing is pinned that cannot then be built
NAME = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?")
# a big catalogue is the point here, so only a mirror of the world is excluded
ENTRY_CAP = 2000


def is_manifest(path: str) -> bool:
    return path in MANIFESTS


def fields_of(value: object) -> dict[str, object]:
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
    """A relative source as a repository-relative directory."""
    # Copilot takes `plugins/foo`, where Claude Code insists on `./plugins/foo`
    path = source.strip().removeprefix("./")
    if not path or ":" in path:
        return None
    if "/" not in path and root:  # only a bare name resolves against the root
        path = posixpath.join(root, path)
    path = posixpath.normpath(path)
    if path == "." or path.startswith("/") or ".." in path.split("/"):
        return None
    return path


def source_of(entry: dict[str, object], root: str) -> tuple[str, str] | None:
    """Where one entry points: `("local", path)` or `("remote", repo)`."""
    source = entry.get("source")
    if isinstance(source, str):
        if "://" in source:
            found = repo_at(source)
            return ("remote", found) if found else None
        local = local_of(source, root)
        return ("local", local) if local else None
    fields = fields_of(source)
    repo = fields.get("repo")
    if fields.get("source") == "github" and isinstance(repo, str):
        found = repo_named(repo)
        return ("remote", found) if found else None
    for key in ("url", "repo"):
        value = fields.get(key)
        if isinstance(value, str) and (found := repo_at(value)):
            return "remote", found
    return None


class Manifests(typing.NamedTuple):
    names: list[str]
    local: list[str]
    remote: list[str]
    entries: int


def read_manifests(owner_repo: str, blobs: dict[str, bytes]) -> Manifests:
    names: list[str] = []
    sources = {"local": set[str](), "remote": set[str]()}
    entries = 0
    for path in MANIFESTS:
        blob = blobs.get(path)
        if blob is None:
            # absent, or a symlink, which archive_read returns None for
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
            # the same manifest, reached through a second documented location
            continue
        names.append(name)
        plugins = manifest.get("plugins")
        if not isinstance(plugins, list):
            continue
        listed = typing.cast(list[object], plugins)
        entries += len(listed)
        root = plugin_root(manifest)
        for item in listed:
            if found := source_of(fields_of(item), root):
                sources[found[0]].add(found[1])
    local, remote = sources["local"], sources["remote"]
    return Manifests(sorted(names), sorted(local), sorted(remote), entries)


def marketplaces_in(
    owner_repo: str, _: list[str], blobs: dict[str, bytes], rule: Source
) -> Packaged:
    found = read_manifests(owner_repo, blobs)
    if not found.names:
        log.info("nothing to package in %s: no marketplace", owner_repo)
    elif is_mirror(rule, found.entries > ENTRY_CAP):
        log.info("nothing to package in %s: a mirror", owner_repo)
        return [], {"entries": {"local": [], "remote": []}}
    # the whole repository, because an entry's source is relative to it
    return found.names, {
        "entries": {"local": found.local, "remote": found.remote}
    }


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [shard] if re.fullmatch(r"[1-9]\d*/[1-9]\d*", shard):
            engine.run(shard, marketplaces_in, want=is_manifest)
        case _:
            sys.exit("copilot-marketplaces-update.py <index/total>")
