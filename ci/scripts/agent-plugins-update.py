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

KIND = "agent-plugins"
CONCURRENCY = 4
SNAPSHOTS: Snapshots[Snapshot] = Snapshots(data_dir(KIND), "github.com")
engine = Engine(
    pool(github_token_headers(), backoff=2, maxsize=CONCURRENCY),
    SNAPSHOTS,
    CONCURRENCY,
)

MANIFEST = "plugin.json"
# the marker for the kind: the other plugin formats keep a plugin.json too
SCHEMAS = frozenset(
    f"https://agent-plugins.org/schemas/{spec}/plugin.schema.json"
    for spec in ("1.0.0", "1.1.0")
)
# far over what agent-skills allows, because a catalogue is the point here
CATALOGUE = 1000
# one past the cap, so a repository over it still reads as over it
READS = CATALOGUE + 1


def is_manifest(path: str) -> bool:
    directory, _, name = path.rpartition("/")
    return name == MANIFEST and not is_vendored(directory)


def plugin_at(path: str, blob: bytes) -> str | None:
    try:
        manifest = json.loads(blob)
    except ValueError:
        return None
    marker = manifest.get("$schema") if isinstance(manifest, dict) else None
    if marker not in SCHEMAS:
        return None
    return posixpath.dirname(path) or "."


def plugins_in(
    owner_repo: str, _: list[str], blobs: dict[str, bytes], rule: Source
) -> Packaged:
    repo = owner_repo.split("/")[1]
    found = [p for path, blob in blobs.items() if (p := plugin_at(path, blob))]
    # a manifest under a plugin is a client extension directory, not a plugin
    paths = select_canonical(repo, outermost(found))
    if not paths:
        log.info("nothing to package in %s: no plugins", repo)
    elif is_mirror(rule, len(paths) > CATALOGUE):
        log.info("nothing to package in %s: a mirror", repo)
        return [], {}
    return paths, {}


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [shard] if re.fullmatch(r"[1-9]\d*/[1-9]\d*", shard):
            engine.run(shard, plugins_in, want=is_manifest, reads=READS)
        case _:
            sys.exit("agent-plugins-update.py <index/total>")
