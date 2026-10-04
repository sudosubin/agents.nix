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
    directories,
    github_token_headers,
    is_manifest_dir,
    is_mirror,
    is_vendored,
    pool,
    select_canonical,
)

log = logging.getLogger(__name__)

KIND = "copilot-plugins"
CONCURRENCY = 4
SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY),
    SNAPSHOTS,
    CONCURRENCY,
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
WANTED = set(MANIFESTS) | set(MARKETPLACES)
SUFFIXES = tuple(f"/{location}" for location in MANIFESTS)
# the name pattern the schemas hold, so nothing is pinned the hook would refuse
NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")
# github/awesome-copilot, the largest marketplace there is, holds 100
CATALOGUE = 500


def is_manifest(path: str) -> bool:
    return path in WANTED or path.endswith(SUFFIXES)


def root_of(path: str, location: str) -> str | None:
    if path == location:
        return "."
    suffix = f"/{location}"
    return path[: -len(suffix)] if path.endswith(suffix) else None


def declared_roots(files: list[str]) -> dict[str, str]:
    """Every directory holding a manifest, with the one Copilot would load."""
    found: dict[str, str] = {}
    for path in files:
        for location in NESTED:
            root = root_of(path, location)
            if root is None:
                continue
            # .codex-plugin and its kind are the plugin above them, not a plugin
            if location == "plugin.json" and is_manifest_dir(root):
                break
            best = found.get(root)
            if best is None or MANIFESTS.index(location) < MANIFESTS.index(
                best
            ):
                found[root] = location
            break
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


def refused(root: str, location: str, blobs: dict[str, bytes]) -> str | None:
    """Why check-hook.sh would refuse this root's manifest, if it would."""
    path = location if root == "." else f"{root}/{location}"
    # a manifest the read budget missed is the hook's to judge, not this run's
    if (data := blobs.get(path)) is None:
        return None
    try:
        manifest = json.loads(data)
    except ValueError:
        return None
    name = (
        typing.cast(dict[str, typing.Any], manifest).get("name")
        if isinstance(manifest, dict)
        else None
    )
    if not isinstance(name, str):
        return f"{location} names nothing"
    refusing = (
        len(name) > 64
        or "--" in name
        or ".." in name
        or NAME.fullmatch(name) is None
    )
    return f'{location} names "{name}"' if refusing else None


# no outermost(): a root plugin's marketplace lists children that are real too
def plugins_in(
    owner_repo: str, files: list[str], blobs: dict[str, bytes], rule: Source
) -> Packaged:
    repo = owner_repo.split("/")[1]
    here = directories(files)
    declared = declared_roots(files)
    listed = [path for path in listed_roots(blobs) if path in here]
    roots: list[str] = []
    for path in dict.fromkeys([*declared, *listed]):
        if is_vendored(path):
            continue
        location = declared.get(path)
        reason = refused(path, location, blobs) if location else None
        if reason:
            log.info("not packaging %s/%s: %s", repo, path, reason)
            continue
        roots.append(path)
    paths = select_canonical(repo, roots)
    if not paths:
        log.info("nothing to package in %s: no plugins", repo)
    elif is_mirror(rule, len(paths) >= CATALOGUE):
        log.info("nothing to package in %s: a catalogue", repo)
        return [], {}
    return paths, {}


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [shard] if re.fullmatch(r"[1-9]\d*/[1-9]\d*", shard):
            # every manifest this run could package, so refused() sees them all
            reads = CATALOGUE + len(MARKETPLACES)
            engine.run(shard, plugins_in, want=is_manifest, reads=reads)
        case _:
            sys.exit("copilot-plugins-update.py <index/total>")
