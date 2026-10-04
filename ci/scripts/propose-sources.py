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

import pathlib
import subprocess
import sys

from agents.nix import NOTE, blob_at, git, propose, put_file

TITLES = {
    "update": "chore: update {kind} sources",
    "reconcile": "chore: reconcile {kind} sources with github",
}


def main(action: str, path: pathlib.Path, command: str) -> None:
    kind = path.parent.name
    branch = f"sources/{kind}-{action}"
    title = TITLES[action].format(kind=kind)

    # a required check guards main, so this lands through a pull request
    for _ in range(3):
        git("fetch", "-q", "origin", "main")
        git("checkout", "-q", "-f", "-B", "main", "origin/main")
        refresh = subprocess.run(["bash", "-o", "pipefail", "-c", command])
        if refresh.returncode != 0:
            sys.exit(f"{command!r} failed with {refresh.returncode}")
        unchanged = ["git", "diff", "--quiet", "--", str(path)]
        if subprocess.run(unchanged, check=False).returncode == 0:
            return

        # git cuts the branch; the api writes the commit, which signs it
        git("push", "-q", "--force", "origin", f"HEAD:refs/heads/{branch}")
        # the blob main had is the compare-and-swap: a newer main refuses it
        try:
            put_file(
                str(path),
                branch,
                title,
                path.read_bytes(),
                blob_at("HEAD", str(path)),
            )
        except subprocess.CalledProcessError:
            print(f"::notice::main moved under {path}; trying again")
            continue
        propose(branch, title, NOTE)
        return
    print(f"::warning::main kept moving; leaving {kind} to the next run")


if __name__ == "__main__":
    match sys.argv[1:]:
        case [action, path, command] if action in TITLES:
            main(action, pathlib.Path(path), command)
        case _:
            sys.exit(
                "propose-sources.py update|reconcile <sources.json> <command>"
            )
