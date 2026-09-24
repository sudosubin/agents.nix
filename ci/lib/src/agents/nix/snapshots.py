import dataclasses
import json
import pathlib
import posixpath
import typing

import jsonyx


class Pin(typing.TypedDict, closed=True):
    rev: str
    version: str
    hash: str


class Snapshot(typing.TypedDict, closed=True):
    """<forge>/<owner>/<repo>.json"""

    rev: str
    version: str
    hash: str
    paths: dict[str, list[str]]
    at: typing.NotRequired[dict[str, Pin]]


@dataclasses.dataclass(frozen=True, slots=True)
class Snapshots:
    directory: pathlib.Path
    forge: str

    def path(self, owner_repo: str) -> pathlib.Path:
        # forges are case-insensitive
        return self.directory / self.forge / (owner_repo.lower() + ".json")

    def read(self, owner_repo: str) -> Snapshot | None:
        try:
            text = self.path(owner_repo).read_text()
        except FileNotFoundError:
            return None
        return typing.cast(Snapshot, json.loads(text))

    def write(self, owner_repo: str, snapshot: Snapshot) -> None:
        path = self.path(owner_repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(jsonyx.dumps(snapshot, indent=2, indent_leaves=False))


def group_paths(paths: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for path in paths:
        directory, name = posixpath.split(path)
        grouped.setdefault(directory, []).append(name)
    return {d: sorted(names) for d, names in sorted(grouped.items())}


def flatten_paths(grouped: dict[str, list[str]]) -> list[str]:
    return sorted(
        posixpath.join(directory, name)
        for directory, names in grouped.items()
        for name in names
    )
