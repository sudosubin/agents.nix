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
    pool,
    shard_of,
)

log = logging.getLogger(__name__)

KIND = "agent-skills"
SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
CONCURRENCY = 4
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY), SNAPSHOTS
)

MARKER = "SKILL.md"
PLUGIN_MANIFEST = "plugin.json"
SEARCH_IGNORE_DIRS = set(
    """
    node_modules .git dist build out target .next .nuxt .cache coverage
    vendor __pycache__ .venv venv .tox .mypy_cache .pytest_cache .gradle
    .idea .bundle .pnpm-store bin obj Pods DerivedData
    """.split()
)
# follows vercel-labs/skills' AGENT_PROJECT_SKILL_DIRS, plus .agent/skills
TOOL_DIRS = """
    agent agents claude cline codebuddy codex commandcode continue factory
    github goose grok iflow junie kilo kilocode kimchi kiro minimax mux
    neovate opencode openhands pi qoder roo trae windsurf zcode zencoder
""".split()
LOAD_DIRS = [
    ".",
    "skills",
    "skills/.curated",
    "skills/.experimental",
    "skills/.system",
    ".posit/assistant/skills",
    *(f".{tool}/skills" for tool in TOOL_DIRS),
]


def find_skills(tree: list[str]) -> list[str]:
    found = []
    for path in tree:
        directory, _, name = path.rpartition("/")
        if name == MARKER and SEARCH_IGNORE_DIRS.isdisjoint(
            directory.split("/")
        ):
            found.append(directory or ".")
    return sorted(found)


def load_dirs(tree: list[str]) -> list[str]:
    plugins = set()
    for path in tree:
        directory, _, name = path.rpartition("/")
        if name != PLUGIN_MANIFEST:
            continue
        head, _, tail = directory.rpartition("/")
        nested = tail.startswith(".") and tail.endswith("-plugin")
        plugins.add(head if nested else directory)
    return LOAD_DIRS + [
        posixpath.join(root, "skills") for root in sorted(plugins) if root
    ]


def container_of(path: str, dirs: list[str]) -> int:
    # reaches 3 levels into a container, 1 at the root
    nested = (
        n
        for n, directory in enumerate(dirs)
        if path == directory
        or (directory == "." and "/" not in path)
        or (
            path.startswith(f"{directory}/")
            and path.count("/", len(directory)) <= 3
        )
    )
    return next(nested, len(dirs))


def select_canonical(repo: str, paths: list[str], dirs: list[str]) -> list[str]:
    def rank(path: str) -> tuple[int, int, str]:
        priority = 0 if "/" not in path else container_of(path, dirs)
        depth = 0 if path == "." else path.count("/") + 1
        return priority, depth, path

    chosen: dict[str, str] = {}
    for path in sorted(paths, key=rank):
        name = repo if path == "." else posixpath.basename(path)
        chosen.setdefault(name.lower(), path)
    return sorted(chosen.values())


def is_mirror(paths: list[str], dirs: list[str], rule: Source) -> bool:
    catalogue, vendored, ratio = 100, 20, 0.8
    if (skip := rule.get("skip")) is not None:
        return bool(skip)
    if len(paths) >= catalogue:
        return True
    outside = [p for p in paths if container_of(p, dirs) == len(dirs)]
    return len(paths) >= vendored and len(outside) > len(paths) * ratio


def skills_in(repo: str, files: list[str], rule: Source) -> list[str]:
    dirs = load_dirs(files)
    paths = select_canonical(repo, find_skills(files), dirs)
    if not paths:
        log.info("nothing to package in %s: no skills", repo)
    elif is_mirror(paths, dirs, rule):
        log.info("nothing to package in %s: a mirror", repo)
        return []
    return paths


def update_repo(owner_repo: str, target: Target) -> Snapshot | None:
    candidate, extra, rule = target
    ref = candidate.ref
    log.info("processing %s@%s", owner_repo, candidate.tag or ref[:7])
    try:
        digest, files, _ = engine.fetch_tree(owner_repo, candidate.archive_ref)
        paths = skills_in(owner_repo.split("/")[1], files, rule)
        at = engine.pins_at(owner_repo, paths, candidate, extra)
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
            sys.exit("agent-skills-update.py <index/total>")
