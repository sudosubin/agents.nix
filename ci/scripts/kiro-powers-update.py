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
    is_manifest_dir,
    is_mirror,
    is_vendored,
    outermost,
    pool,
    select_canonical,
)

log = logging.getLogger(__name__)

KIND = "kiro-powers"
CONCURRENCY = 4
SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY),
    SNAPSHOTS,
    CONCURRENCY,
)

# the legacy POWER.md is still most of the registry, so both formats count
MARKERS = frozenset({"POWER.md", "plugin.json"})
# the registry itself lists about thirty; past this it is somebody's scrape
CATALOGUE = 200


def find_powers(tree: list[str]) -> list[str]:
    found = set()
    for path in tree:
        directory, _, name = path.rpartition("/")
        # a client's manifest directory belongs to the power above it
        if (
            name in MARKERS
            and not is_vendored(directory)
            and not is_manifest_dir(directory)
        ):
            found.add(directory or ".")
    return sorted(found)


def powers_in(
    owner_repo: str, files: list[str], _: dict[str, bytes], rule: Source
) -> Packaged:
    repo = owner_repo.split("/")[1]
    # a marker below a power belongs to that power
    paths = select_canonical(repo, outermost(find_powers(files)))
    if not paths:
        log.info("nothing to package in %s: no powers", repo)
    elif is_mirror(rule, len(paths) >= CATALOGUE):
        log.info("nothing to package in %s: %d powers", repo, len(paths))
        return [], {}
    return paths, {}


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [shard] if re.fullmatch(r"[1-9]\d*/[1-9]\d*", shard):
            engine.run(shard, powers_in)
        case _:
            sys.exit("kiro-powers-update.py <index/total>")
