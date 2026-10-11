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

import collections.abc
import itertools
import json
import logging
import pathlib
import posixpath
import sys
import typing

import urllib3
from agents.nix import (
    Source,
    configure_logging,
    data_dir,
    discovery,
    github_token_headers,
    pool,
)
from agents.nix.github import (
    TREE_FIELDS,
    Entry,
    Root,
    objects,
    read_blob,
    read_link,
    root_trees,
)
from agents.nix.snapshots import (
    CODEX_MANIFESTS as MANIFESTS,
)
from agents.nix.snapshots import (
    codex_is_mirror as is_mirror,
)
from agents.nix.snapshots import (
    codex_local_of as local_of,
)
from agents.nix.snapshots import (
    codex_manifests_in as manifests_in,
)
from agents.nix.snapshots import (
    codex_marketplaces_in as marketplaces_in,
)
from agents.nix.snapshots import (
    codex_named as named,
)
from agents.nix.snapshots import (
    codex_sources_of as sources_of,
)

log = logging.getLogger(__name__)

http = pool(github_token_headers())


def valid_locals(
    http: urllib3.PoolManager,
    public: urllib3.PoolManager,
    roots: dict[str, Root],
    paths: dict[str, set[str]],
) -> dict[str, set[str]]:
    for repo, root in roots.items():
        if root.get("entries") is None:
            raise OSError(f"Git tree entries unavailable for {repo}")
    cache: dict[tuple[str, str], dict[str, Entry] | None] = {
        (repo, ""): {e["name"]: e for e in root.get("entries") or []}
        for repo, root in roots.items()
    }
    links: dict[tuple[str, str], str | None] = {}
    valid: dict[str, set[str]] = {repo: set() for repo in paths}
    pending = {
        (repo, path): (path, {path})
        for repo, local in paths.items()
        for path in local
    }
    while pending:
        probes = {
            (repo, f"{roots[repo]['oid']}:{parent}")
            for (repo, _), (path, _) in pending.items()
            for i in range(len(path.split("/")))
            if (parent := "/".join(path.split("/")[:i]))
            and (repo, parent) not in cache
        }
        for repo, expression, obj in objects(http, sorted(probes), TREE_FIELDS):
            parent = expression.partition(":")[2]
            if (
                obj is not None
                and obj.get("kind") == "Tree"
                and obj.get("entries") is None
            ):
                raise OSError(
                    f"Git tree entries unavailable for {repo}:{parent}"
                )
            cache[repo, parent] = (
                {e["name"]: e for e in obj.get("entries") or []}
                if obj is not None and obj.get("kind") == "Tree"
                else None
            )
        again: dict[tuple[str, str], tuple[str, set[str]]] = {}
        for (repo, original), (path, seen) in pending.items():
            if path == ".":
                valid[repo].add(original)
                continue
            parts = path.split("/")
            for i, part in enumerate(parts):
                parent = "/".join(parts[:i])
                entry = (cache[repo, parent] or {}).get(part)
                if entry is None:
                    break
                here = "/".join(parts[: i + 1])
                if entry["mode"] == 0o120000:
                    key = repo, here
                    if key not in links:
                        links[key] = read_link(
                            public, repo, roots[repo]["rev"], here, entry
                        )
                    target = links[key]
                    if not target or target.startswith("/") or "\0" in target:
                        break
                    resolved = posixpath.normpath(
                        posixpath.join(parent, target, *parts[i + 1 :])
                    )
                    if (
                        resolved == ".."
                        or resolved.startswith("../")
                        or resolved in seen
                        or len(seen) > 40
                    ):
                        break
                    again[repo, original] = resolved, seen | {resolved}
                    break
                if entry["type"] == "tree":
                    if i == len(parts) - 1:
                        valid[repo].add(original)
                elif i == len(parts) - 1 and entry["mode"] in {
                    0o100644,
                    0o100755,
                }:
                    valid[repo].add(original)
                    break
                else:
                    break
        pending = again
    return valid


def crawl(
    http: urllib3.PoolManager, sources_file: pathlib.Path
) -> collections.abc.Iterator[str]:
    if not sources_file.exists():
        log.info("%s does not exist yet", sources_file)
        return
    sources = typing.cast(
        dict[str, Source], json.loads(sources_file.read_text())
    )
    active = {
        repo.removeprefix("github:"): rule
        for repo, rule in sorted(sources.items())
        if repo.startswith("github:")
        and "deleted" not in rule
        and not rule.get("skip")
    }
    public = pool()
    for batch in itertools.batched(active, 25, strict=False):
        roots = dict(root_trees(http, batch))
        catalogs: dict[str, tuple[list[str], dict[str, bytes]]] = {}
        local: dict[str, set[str]] = {}
        for repo, _, obj in objects(
            http,
            (
                (repo, f"{root['oid']}:.agents/plugins")
                for repo, root in roots.items()
            ),
            TREE_FIELDS,
        ):
            if obj is None or obj.get("kind") != "Tree":
                continue
            if obj.get("entries") is None:
                raise OSError(
                    f"Git tree entries unavailable for {repo}:.agents/plugins"
                )
            entries = {e["name"]: e for e in obj.get("entries") or []}
            files = [
                path
                for path in MANIFESTS
                if (entry := entries.get(posixpath.basename(path))) is not None
                and entry["type"] == "blob"
            ]
            if not files:
                continue
            manifest = files[0]
            blob = read_blob(
                public,
                repo,
                roots[repo]["rev"],
                manifest,
                entries[posixpath.basename(manifest)],
            )
            blobs = {manifest: blob} if blob is not None else {}
            found = manifests_in(repo, files, blobs)
            if not found:
                continue
            document = found[0][1]
            if named(document) is None or is_mirror(
                len(sources_of(document)), active[repo]
            ):
                continue
            catalogs[repo] = files, blobs
            local[repo] = {
                path
                for source in sources_of(document)
                if (path := local_of(source)) is not None
            }
        confirmed = valid_locals(http, public, roots, local)
        for repo, (files, blobs) in catalogs.items():
            _, entries = marketplaces_in(
                repo, files + sorted(confirmed[repo]), blobs, active[repo]
            )
            if entries["local"]:
                yield f"github:{repo}"
            yield from entries["remote"]


def fetch_marketplace_crawl() -> collections.abc.Iterator[str]:
    return crawl(http, data_dir("codex-marketplaces") / "sources.json")


def fetch_github_code() -> collections.abc.Iterator[str]:
    return discovery.code(http, ['".codex-plugin" filename:plugin.json'])


def fetch_github_topics() -> list[str]:
    return discovery.topics(http, ["codex-plugin"])


FETCHERS = {
    "marketplace-crawl": fetch_marketplace_crawl,
    "github-code": fetch_github_code,
    "github-topics": fetch_github_topics,
}


def main(site: str, out: pathlib.Path) -> None:
    discovery.write_scan(out, site, FETCHERS[site]())


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [site, out] if site in FETCHERS:
            main(site, pathlib.Path(out))
        case _:
            sys.exit("codex-plugins-scan.py <site> <out.json>")
