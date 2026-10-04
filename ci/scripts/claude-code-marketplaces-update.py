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
    flatten_paths,
    github_token_headers,
    is_mirror,
    is_vendored,
    pool,
    repo_at,
    repo_named,
)

log = logging.getLogger(__name__)


class Listing(Snapshot):
    """A marketplace snapshot also records what its entries point at."""

    entries: dict[str, list[str]]


KIND = "claude-code-marketplaces"
CONCURRENCY = 4
SNAPSHOTS: Snapshots[Listing] = Snapshots(data_dir(KIND), "github.com")
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY),
    SNAPSHOTS,
    CONCURRENCY,
)

MANIFEST = ".claude-plugin/marketplace.json"
# what claude code refuses in a name; case is not one of them
UNUSABLE = re.compile(r"[\x00-\x20\x7f-\x9f؜‎‏‪-‮⁦-⁩/\\]")
# more marketplaces than this is a directory of other people's
MOST = 8


def manifest_dir(path: str) -> str | None:
    """The directory a marketplace is packaged from, `""` at the root."""
    if path == MANIFEST:
        return ""
    if path.endswith(f"/{MANIFEST}"):
        return path[: -len(MANIFEST) - 1]
    return None


def wanted(path: str) -> bool:
    directory = manifest_dir(path)
    return directory is not None and not is_vendored(directory)


def name_of(manifest: dict[str, typing.Any]) -> str | None:
    name = manifest.get("name")
    if not isinstance(name, str) or not name or name == ".":
        return None
    if ".." in name or UNUSABLE.search(name):
        return None
    return name


def local_of(directory: str, root: str, source: str) -> str | None:
    """A relative source as a path from the repository root."""
    if source.startswith("./"):
        path = source[2:]
    elif root:
        # a bare name is resolved against pluginRoot, and only then
        path = posixpath.join(root, source)
    else:
        return None
    joined = posixpath.join(directory, path)
    if joined.startswith("/") or ".." in joined.split("/"):
        return None
    return posixpath.normpath(joined)


def remote_of(source: dict[str, typing.Any]) -> str | None:
    match source.get("source"):
        case "github":
            repo = source.get("repo")
            return repo_named(repo) if isinstance(repo, str) else None
        case "git-subdir" | "url":
            url = source.get("url")
            return repo_at(url) if isinstance(url, str) else None
        case _:
            # npm, archive and command point at no repository
            return None


def entries_of(
    directory: str, manifest: dict[str, typing.Any]
) -> tuple[set[str], set[str]]:
    metadata = manifest.get("metadata")
    root = metadata.get("pluginRoot") if isinstance(metadata, dict) else None
    local: set[str] = set()
    remote: set[str] = set()
    plugins = manifest.get("plugins")
    for plugin in plugins if isinstance(plugins, list) else []:
        source = plugin.get("source") if isinstance(plugin, dict) else None
        if isinstance(source, str):
            if (path := local_of(directory, root or "", source)) is not None:
                local.add(path)
        elif isinstance(source, dict) and (found := remote_of(source)):
            remote.add(found)
    return local, remote


def marketplaces_in(
    owner_repo: str, _: list[str], blobs: dict[str, bytes], rule: Source
) -> Packaged:
    repo = owner_repo.split("/")[1]
    found: dict[str, set[str]] = {}
    local: set[str] = set()
    remote: set[str] = set()
    for path, blob in sorted(blobs.items()):
        directory = manifest_dir(path)
        try:
            manifest = json.loads(blob)
        except ValueError as error:
            log.info("skipped %s of %s: %s", path, repo, error)
            continue
        if directory is None or not isinstance(manifest, dict):
            continue
        name = name_of(manifest)
        if name is None:
            # claude code would refuse it too, so there is nothing to package
            log.info(
                "skipped %s of %s: no name it would load under", path, repo
            )
            continue
        found.setdefault(directory, set()).add(name)
        here, there = entries_of(directory, manifest)
        local |= here
        remote |= there

    if is_mirror(rule, sum(map(len, found.values())) > MOST):
        log.info("nothing to package in %s: a mirror", repo)
        return [], {"entries": {"local": [], "remote": []}}
    if not found:
        log.info("nothing to package in %s: no marketplaces", repo)
    # keyed `<directory>/<name>`, the way the engine matches its globs
    paths = flatten_paths({d: sorted(names) for d, names in found.items()})
    return paths, {
        "entries": {"local": sorted(local), "remote": sorted(remote)}
    }


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [shard] if re.fullmatch(r"[1-9]\d*/[1-9]\d*", shard):
            engine.run(shard, marketplaces_in, want=wanted)
        case _:
            sys.exit("claude-code-marketplaces-update.py <index/total>")
