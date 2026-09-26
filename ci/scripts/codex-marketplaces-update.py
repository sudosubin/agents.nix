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
    entries: dict[str, list[str]]
    at: typing.NotRequired[dict[str, Pin]]


KIND = "codex-marketplaces"
SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
CONCURRENCY = 4
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY), SNAPSHOTS
)

# codex-rs/core-plugins/src/marketplace.rs, MARKETPLACE_MANIFEST_RELATIVE_PATHS,
# in the order codex resolves them. A repository that carries more than one gets
# an attribute for each, because all of them describe the same root.
MANIFESTS = (
    ".agents/plugins/marketplace.json",
    ".agents/plugins/api_marketplace.json",
    ".claude-plugin/marketplace.json",
    ".cursor-plugin/marketplace.json",
)
WANTED = frozenset(MANIFESTS)
# the sources that name another repository rather than this one
REMOTE = frozenset({"git-subdir", "github", "url"})
# codex-rs/skills/src/assets/samples/plugin-creator/scripts/
# identifier_validation.py, validate_marketplace_name. It is deliberately not
# the plugin rule beside it, which allows dots as segment separators. A slash
# cannot pass it either, which group_paths would read as a directory.
NAME = re.compile(r"[A-Za-z0-9_-]+")
# a list this long is an index of everything someone could find, not a curated
# marketplace; `skip: false` in sources.json overrides it
ENTRY_CAP = 2000


def wanted(path: str) -> bool:
    return path in WANTED


def manifests_in(
    owner_repo: str, blobs: dict[str, bytes]
) -> list[tuple[str, dict[str, typing.Any]]]:
    found = []
    for manifest in MANIFESTS:
        blob = blobs.get(manifest)
        if blob is None:
            continue
        try:
            document = json.loads(blob)
        except ValueError as error:
            log.info("skipped %s/%s: %s", owner_repo, manifest, error)
            continue
        if isinstance(document, dict):
            found.append((
                manifest,
                typing.cast(dict[str, typing.Any], document),
            ))
    return found


def named(document: dict[str, typing.Any]) -> str | None:
    """A manifest's own name, when it makes a usable attribute."""
    name = document.get("name")
    if not isinstance(name, str) or len(name) > 64:
        return None
    return name if NAME.fullmatch(name) else None


def sources_of(
    document: dict[str, typing.Any],
) -> list[dict[str, typing.Any]]:
    plugins = document.get("plugins")
    if not isinstance(plugins, list):
        return []
    return [
        entry["source"]
        for entry in typing.cast(list[typing.Any], plugins)
        if isinstance(entry, dict) and isinstance(entry.get("source"), dict)
    ]


def local_of(source: dict[str, typing.Any]) -> str | None:
    """A local source as a repository-relative directory."""
    if source.get("source") != "local":
        return None
    path = source.get("path")
    if not isinstance(path, str) or path.startswith("/"):
        return None
    split = typing.cast(list[str], path.split("/"))
    parts = [part for part in split if part not in {"", "."}]
    return None if not parts or ".." in parts else "/".join(parts)


def github_repo(url: str) -> str | None:
    # git@github.com:owner/repo.git is a url everywhere but to urlparse
    scp = re.fullmatch(r"[\w.+-]+@([\w.-]+):(.+)", url)
    parsed = urlparse(f"https://{scp[1]}/{scp[2]}" if scp else url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return None
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if len(parts) < 2 or not all(parts[:2]):
        return None
    return f"github:{parts[0].lower()}/{parts[1].lower()}"


def remote_of(source: dict[str, typing.Any]) -> str | None:
    """A remote source as `github:owner/repo`, where it is one."""
    if source.get("source") not in REMOTE:
        return None
    url = source.get("url")
    if isinstance(url, str):
        return github_repo(url)
    # no first-party manifest ships a `github` source to copy from, so only the
    # obvious spelling is read; anything else is dropped rather than guessed at
    repo = source.get("repo")
    if isinstance(repo, str) and re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return f"github:{repo.lower()}"
    return None


def tree_dirs(files: list[str]) -> set[str]:
    """Every directory the archive holds, which lists only its files."""
    dirs: set[str] = set()
    for path in files:
        parts = path.split("/")[:-1]
        dirs.update("/".join(parts[: n + 1]) for n in range(len(parts)))
    return dirs


def marketplaces_in(
    owner_repo: str, files: list[str], blobs: dict[str, bytes], rule: Source
) -> tuple[list[str], dict[str, list[str]]]:
    dirs = tree_dirs(files)
    names: dict[str, str] = {}
    local: set[str] = set()
    remote: set[str] = set()
    counted = 0
    for manifest, document in manifests_in(owner_repo, blobs):
        name = named(document)
        if name is None:
            log.info("skipped %s/%s: unusable name", owner_repo, manifest)
            continue
        sources = sources_of(document)
        here = {p for source in sources if (p := local_of(source))}
        # the check hook refuses a manifest that points at a directory it does
        # not ship, so an attribute that says so would only ever fail to build
        if missing := sorted(here - dirs):
            log.info(
                "skipped %s/%s: %d paths missing, first %s",
                owner_repo,
                manifest,
                len(missing),
                missing[0],
            )
            continue
        counted += len(sources)
        names.setdefault(name.lower(), name)
        local |= here
        remote |= {r for source in sources if (r := remote_of(source))}

    if not names:
        log.info("nothing to package in %s: no marketplace", owner_repo)
    elif is_dump(counted, rule):
        log.info("nothing to package in %s: an index", owner_repo)
        return [], {"local": [], "remote": []}
    return sorted(names.values()), {
        "local": sorted(local),
        "remote": sorted(remote),
    }


def is_dump(entries: int, rule: Source) -> bool:
    if (skip := rule.get("skip")) is not None:
        return bool(skip)
    return entries > ENTRY_CAP


def update_repo(owner_repo: str, target: Target) -> Snapshot | None:
    candidate, extra, rule = target
    ref = candidate.ref
    log.info("processing %s@%s", owner_repo, candidate.tag or ref[:7])
    try:
        digest, files, blobs = engine.fetch_tree(
            owner_repo, candidate.archive_ref, want=wanted
        )
        names, entries = marketplaces_in(owner_repo, files, blobs, rule)
        globs = {g: p for g, p in extra.items() if p.ref != ref}
        # a marketplace is the whole root, so a glob can only ever match its
        # name; it is computed anyway because plan() decides the same way
        at = {
            path: p.written()
            | {"hash": engine.fetch_tree(owner_repo, p.archive_ref)[0]}
            for path, p in sorted(pin_paths(names, globs).items())
        }
    except OSError as error:
        log.warning("failed to fetch %s@%s: %s", owner_repo, ref[:7], error)
        return None
    fresh = candidate.written() | {
        "hash": digest,
        "paths": group_paths(names),
        "entries": entries,
    }
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
            sys.exit("codex-marketplaces-update.py <index/total>")
