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
import re
import sys

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
    is_vendored,
    outermost,
    pool,
    select_canonical,
)

log = logging.getLogger(__name__)

KIND = "codex-plugins"
CONCURRENCY = 4
SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY),
    SNAPSHOTS,
    CONCURRENCY,
)

MANIFEST = "plugin.json"
# .claude-plugin/ is codex's ALTERNATE_PLUGIN_MANIFEST_RELATIVE_PATH
MANIFEST_DIRS = (".codex-plugin", ".claude-plugin")
# a repository with hundreds of plugins is a marketplace, which is worth having
CATALOGUE = 500


def find_plugins(tree: list[str]) -> list[str]:
    found = set()
    for path in tree:
        directory, _, name = path.rpartition("/")
        root, _, holder = directory.rpartition("/")
        if name != MANIFEST or holder not in MANIFEST_DIRS:
            continue
        # vendored third-party code, not build output: a plugin may be `dist`
        if is_vendored(root):
            continue
        found.add(root or ".")
    return sorted(found)


def plugins_in(
    owner_repo: str, files: list[str], _: dict[str, bytes], rule: Source
) -> Packaged:
    repo = owner_repo.split("/")[1]
    # a plugin root inside another one already ships inside its parent
    paths = select_canonical(repo, outermost(find_plugins(files)))
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
            engine.run(shard, plugins_in)
        case _:
            sys.exit("codex-plugins-update.py <index/total>")
