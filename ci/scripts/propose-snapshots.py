#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.15"
# dependencies = ["agents.nix"]
#
# [tool.uv.sources]
# "agents.nix" = { path = "../lib", editable = true }
#
# [tool.ty.rules]
# all = "error"
# ///

import json
import logging
import pathlib
import re
import subprocess
import sys
import typing

from agents.nix import (
    NOTE,
    api,
    blob_at,
    configure_logging,
    delete_file,
    git,
    open_pr,
    propose,
    put_file,
)

log = logging.getLogger(__name__)

type Snapshot = dict[str, typing.Any]


def snapshot_at(rev: str, path: str) -> Snapshot | None:
    """A snapshot at a revision, or in the index when `rev` is empty."""
    text = git("show", f"{rev}:{path}", check=False)
    return typing.cast(Snapshot, json.loads(text)) if text else None


def short(rev: str) -> str:
    # a full sha reads as noise; a tag name is already as short as it gets
    return rev[:7] if re.fullmatch(r"[0-9a-f]{40}", rev) else rev


def packaged(kind: str, snapshot: Snapshot) -> str:
    # a kind is named after what it packages: agent-skills holds skills
    unit = kind.rpartition("-")[2].removesuffix("s")
    count = sum(map(len, snapshot["paths"].values()))
    return f"{count} {unit}{'' if count == 1 else 's'}"


def propose_one(file: str, base: str, forges: dict[str, str]) -> None:
    key = file.removeprefix("data/").removesuffix(".json")
    kind, host, owner, repo = key.split("/")
    # the attribute path the flake exposes, which is what nixpkgs titles with
    attr = f"{kind}.{forges.get(host, host)}.{owner}.{repo}"
    branch = "update/" + re.sub(r"\.$", "-dot", key.replace("/.", "/dot-"))

    was = snapshot_at(base, file)
    now = snapshot_at("", file)
    blob = blob_at(base, file)
    # resetting an open PR to main closes it before the new commit arrives
    if open_pr(branch) is None:
        git("push", "-q", "--force", "origin", f"{base}:refs/heads/{branch}")
    elif now is not None:
        try:
            blob = api(f"contents/{file}?ref={branch}")["sha"]
        except subprocess.CalledProcessError:
            blob = None

    if now is None:
        assert was is not None
        title = f"{attr}: remove"
        body = (
            "GitHub no longer serves this repository. "
            f"It was last pinned at `{short(was['rev'])}`."
        )
        delete_file(file, branch, title, base)
    else:
        rev, version = short(now["rev"]), now["version"]
        packages = packaged(kind, now)
        if was is None:
            title = f"{attr}: init at {version}"
            body = (
                f"Pinned at `{rev}`, packaging {packages}.\n\n"
                f"https://github.com/{owner}/{repo}/tree/{rev}"
            )
        else:
            old = short(was["rev"])
            # a revision can move under an unchanged version; say that instead
            moved = (old, rev) if was["version"] == version else None
            title = f"{attr}: {' → '.join(moved or (was['version'], version))}"
            body = (
                f"Pinned revision `{old}` → `{rev}`, packaging {packages}.\n\n"
                f"https://github.com/{owner}/{repo}/compare/{old}...{rev}"
            )
        content = git("show", f":{file}").encode()
        put_file(file, branch, title, content, blob)

    # the title names the revisions, so one left open has to be retitled
    propose(branch, title, f"{NOTE}\n\n{body}", retitle=True)


def main(patches: pathlib.Path) -> None:
    for patch in sorted(patches.glob("*/shard.patch")):
        if patch.stat().st_size:
            git("apply", "--index", str(patch))
    changed = git("diff", "--cached", "--no-renames", "--name-only").split()
    log.info("%d snapshots changed", len(changed))

    base = git("rev-parse", "HEAD").strip()
    # a forge not listed keeps its directory name, which is already unique
    forges = typing.cast(
        dict[str, str], json.loads(pathlib.Path("nix/forges.json").read_text())
    )
    for file in changed:
        propose_one(file, base, forges)


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [patches]:
            main(pathlib.Path(patches))
        case _:
            sys.exit("propose-snapshots.py <patches/>")
