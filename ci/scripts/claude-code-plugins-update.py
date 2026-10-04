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
    depth,
    directories,
    github_token_headers,
    is_mirror,
    is_vendored,
    pool,
    select_canonical,
)

log = logging.getLogger(__name__)

KIND = "claude-code-plugins"
CONCURRENCY = 4
SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY),
    SNAPSHOTS,
    CONCURRENCY,
)

PLUGIN_DIR = ".claude-plugin"
MANIFEST = "plugin.json"
MARKETPLACE = "marketplace.json"
# past this a repository is mirroring a forge rather than curating plugins
CATALOGUE = 1000


def root_of(path: str, name: str) -> str | None:
    directory, _, file = path.rpartition("/")
    root, _, marker = directory.rpartition("/")
    return root or "." if file == name and marker == PLUGIN_DIR else None


def wants_marketplace(path: str) -> bool:
    return root_of(path, MARKETPLACE) is not None


def manifest_roots(files: list[str]) -> list[str]:
    roots = (root_of(path, MANIFEST) for path in files)
    return [root for root in roots if root and not is_vendored(root)]


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


def marketplace_roots(blobs: dict[str, bytes], held: set[str]) -> list[str]:
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
            if resolved is None or is_vendored(resolved):
                continue
            # "." is the repository root, which no file list spells out
            if resolved in held:
                found.append(resolved)
    return found


# no outermost(): a root plugin's marketplace lists children that are real too
def plugins_in(
    owner_repo: str, files: list[str], blobs: dict[str, bytes], rule: Source
) -> Packaged:
    repo = owner_repo.split("/")[1]
    roots = dict.fromkeys(manifest_roots(files), True)
    for path in marketplace_roots(blobs, directories(files)):
        roots.setdefault(path, False)

    def rank(path: str) -> tuple[int, int]:
        # a root that declares itself outranks one a marketplace merely lists
        return 0 if roots[path] else 1, depth(path)

    paths = select_canonical(repo, roots, rank)
    if not paths:
        log.info("nothing to package in %s: no plugins", repo)
    elif is_mirror(rule, len(paths) >= CATALOGUE):
        log.info("nothing to package in %s: a mirror", repo)
        return [], {}
    return paths, {}


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [shard] if re.fullmatch(r"[1-9]\d*/[1-9]\d*", shard):
            engine.run(shard, plugins_in, want=wants_marketplace)
        case _:
            sys.exit("claude-code-plugins-update.py <index/total>")
