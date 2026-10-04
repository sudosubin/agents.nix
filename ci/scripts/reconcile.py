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

import datetime
import json
import logging
import pathlib
import sys
import typing
from concurrent.futures import ThreadPoolExecutor

from agents.nix import (
    RULES,
    Payload,
    Source,
    configure_logging,
    github_token_headers,
    graphql,
    is_gone,
    is_live,
    is_not_found,
    is_too_many_gone,
    pool,
    write_sources,
)

log = logging.getLogger(__name__)

CONCURRENCY = 8
http = pool(github_token_headers(), maxsize=CONCURRENCY)


class Repository(typing.TypedDict, total=False):
    nameWithOwner: str


def resolve(
    sources: list[str],
) -> tuple[dict[str, str], list[str], list[str]]:
    live: dict[str, str] = {}
    absent: list[str] = []
    unknown: list[str] = []
    for start in range(0, len(sources), 200):
        chunk = sources[start : start + 200]
        parts = []
        for index, source in enumerate(chunk):
            owner, _, repo = source.removeprefix("github:").partition("/")
            parts.append(f"""
                r{index}: repository(
                  owner: {json.dumps(owner)}, name: {json.dumps(repo)}
                ) {{ nameWithOwner }}
            """)
        payload: Payload[Repository] = {}
        try:
            payload = graphql(http, "query {\n" + "\n".join(parts) + "\n}")
        except OSError as error:
            log.warning("batch of %d did not answer: %s", len(chunk), error)
        data = payload.get("data")
        if not data:
            unknown += chunk
            continue
        gone = is_not_found(payload)
        for index, source in enumerate(chunk):
            if node := data.get(f"r{index}"):
                live[source] = f"github:{node['nameWithOwner'].lower()}"
            elif f"r{index}" in gone:
                absent.append(source)
            else:
                unknown.append(source)
        log.info("  resolved %d/%d", start + len(chunk), len(sources))
    return live, absent, unknown


def confirm(sources: list[str]) -> dict[str, str]:
    today = datetime.date.today().isoformat()

    def gone(source: str) -> bool:
        url = f"https://github.com/{source.removeprefix('github:')}"
        return is_gone(http, url)

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as workers:
        answers = zip(sources, workers.map(gone, sources), strict=True)
        return {source: today for source, is_it in answers if is_it}


def fold(old: str, new: str, sources: dict[str, Source]) -> None:
    source = typing.cast(dict[str, typing.Any], sources.pop(old))
    target = typing.cast(dict[str, typing.Any], sources.setdefault(new, {}))
    if via := {*target.get("via", []), *source.get("via", [])}:
        target["via"] = sorted(via)
    target["was"] = sorted({
        *target.get("was", []),
        *source.get("was", []),
        old,
    })
    target.pop("deleted", None)
    for field in RULES:
        if field not in source:
            continue
        if field in target and target[field] != source[field]:
            log.warning(
                "%s and %s disagree on %s; kept %s", old, new, field, new
            )
        else:
            target[field] = source[field]


def move(sources: dict[str, Source], live: dict[str, str]) -> int:
    moved = 0
    for name in list(sources):
        now = live.get(name)
        if now == name:
            sources[name].pop("deleted", None)
        elif now is not None:
            fold(name, now, sources)
            moved += 1
    return moved


def free(sources: dict[str, Source], live: dict[str, str]) -> int:
    freed = 0
    for source in sources.values():
        if was := source.get("was"):
            kept = [old for old in was if live.get(old) != old]
            freed += len(was) - len(kept)
            if kept:
                source["was"] = kept
            else:
                del source["was"]
    return freed


def main(path: pathlib.Path) -> None:
    if not path.is_file():
        sys.exit(f"not a file: {path}")
    sources = typing.cast(dict[str, Source], json.loads(path.read_text()))

    held = [old for s in sources.values() for old in s.get("was", [])]
    live, absent, unknown = resolve(sorted({*sources, *held}))
    if unknown:
        log.warning("%d names left as they were", len(unknown))

    fresh = [n for n in absent if n in sources and is_live(sources[n])]
    log.info("confirming %d absent repositories", len(fresh))
    confirmed = confirm(fresh)
    if is_too_many_gone(len(confirmed), len(sources)):
        sys.exit(
            f"[ERROR] {len(confirmed)}/{len(sources)} gone; refusing to delete"
        )

    moved, freed = move(sources, live), free(sources, live)
    for name, when in confirmed.items():
        sources[name]["deleted"] = when
    write_sources(path, sources)
    log.info(
        "%d repositories: %d deleted (+%d), %d moved, %d old names freed",
        len(sources),
        sum(1 for r in sources.values() if "deleted" in r),
        len(confirmed),
        moved,
        freed,
    )


if __name__ == "__main__":
    configure_logging()
    match sys.argv[1:]:
        case [path] if path:
            if not github_token_headers():
                sys.exit("GH_TOKEN is needed to resolve names against GitHub")
            main(pathlib.Path(path))
        case _:
            sys.exit("reconcile.py <sources.json>")
