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

import logging
import posixpath
import re
import sys

from agents.nix import (
    BUILD_DIRS,
    VENDORED_DIRS,
    Engine,
    Packaged,
    Snapshot,
    Snapshots,
    Source,
    configure_logging,
    data_dir,
    depth,
    github_token_headers,
    is_mirror,
    pool,
    select_canonical,
)

log = logging.getLogger(__name__)

KIND = "agent-skills"
CONCURRENCY = 4
SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY),
    SNAPSHOTS,
    CONCURRENCY,
)

MARKER = "SKILL.md"
PLUGIN_MANIFEST = "plugin.json"
SEARCH_IGNORE_DIRS = VENDORED_DIRS | BUILD_DIRS
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


def canonical(repo: str, paths: list[str], dirs: list[str]) -> list[str]:
    def rank(path: str) -> tuple[int, int]:
        priority = 0 if "/" not in path else container_of(path, dirs)
        return priority, depth(path)

    return select_canonical(repo, paths, rank)


def is_vendored_catalogue(paths: list[str], dirs: list[str]) -> bool:
    catalogue, vendored, ratio = 100, 20, 0.8
    if len(paths) >= catalogue:
        return True
    outside = [p for p in paths if container_of(p, dirs) == len(dirs)]
    return len(paths) >= vendored and len(outside) > len(paths) * ratio


def skills_in(
    owner_repo: str, files: list[str], _: dict[str, bytes], rule: Source
) -> Packaged:
    repo = owner_repo.split("/")[1]
    dirs = load_dirs(files)
    paths = canonical(repo, find_skills(files), dirs)
    if not paths:
        log.info("nothing to package in %s: no skills", repo)
    elif is_mirror(rule, is_vendored_catalogue(paths, dirs)):
        log.info("nothing to package in %s: a mirror", repo)
        return [], {}
    return paths, {}


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [shard] if re.fullmatch(r"[1-9]\d*/[1-9]\d*", shard):
            engine.run(shard, skills_in)
        case _:
            sys.exit("agent-skills-update.py <index/total>")
