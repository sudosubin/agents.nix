"""Finding repositories: the scan file, and GitHub search where no site lists
a kind."""

import collections.abc
import json
import logging
import pathlib
import time
import typing

import urllib3

log = logging.getLogger(__name__)

# GitHub answers at most this many results per search, however many it counts.
CEILING = 1000
PAGE = 100
# documented search rate limits, as seconds between requests
PACE = {"repositories": 60 / 30, "code": 60 / 10}


def write_scan(
    out: pathlib.Path, site: str, repos: collections.abc.Iterable[str]
) -> None:
    """One scan file, which qualify.py reads."""
    names = sorted({repo.lower() for repo in repos})
    log.info("%s: %d repositories", site, len(names))
    out.parent.mkdir(parents=True, exist_ok=True)
    listed = {"site": site, "repositories": names}
    out.write_text(json.dumps(listed, indent=0) + "\n")


# a secondary limit answers 403 with Retry-After, which the retry honours
RETRY = urllib3.Retry(
    total=5, backoff_factor=2.0, status_forcelist=[403, 429, 500, 502, 503, 504]
)


def _get(
    http: urllib3.PoolManager, kind: str, query: str, page: int
) -> dict[str, typing.Any]:
    url = f"https://api.github.com/search/{kind}"
    fields = {"q": query, "per_page": str(PAGE), "page": str(page)}
    response = http.request(
        "GET",
        url,
        fields=fields,
        headers={"Accept": "application/vnd.github+json"},
        retries=RETRY,
    )
    time.sleep(PACE[kind])
    if response.status != 200:
        raise OSError(f"HTTP {response.status} for {kind} search {query!r}")
    return typing.cast(dict[str, typing.Any], json.loads(response.data))


def _repos_in(kind: str, payload: dict[str, typing.Any]) -> list[str]:
    items = typing.cast(list[dict[str, typing.Any]], payload.get("items") or [])
    if kind == "repositories":
        return [f"github:{item['full_name']}" for item in items]
    return [
        f"github:{item['repository']['full_name']}"
        for item in items
        if item.get("repository")
    ]


def _drain(
    http: urllib3.PoolManager,
    kind: str,
    query: str,
    first: dict[str, typing.Any],
) -> list[str]:
    """Every result of a query that fits under the ceiling, from page one."""
    total = min(typing.cast(int, first["total_count"]), CEILING)
    repos = _repos_in(kind, first)
    for page in range(2, -(-total // PAGE) + 1):
        try:
            payload = _get(http, kind, query, page)
        except OSError as error:
            log.warning("stopped at page %d: %s", page, error)
            break
        repos += _repos_in(kind, payload)
    return repos


def _shards(
    http: urllib3.PoolManager,
    kind: str,
    base: str,
    field: str,
    low: int,
    high: int | None,
) -> collections.abc.Iterator[tuple[str, dict[str, typing.Any]]]:
    """Split `base` on `field` until every part fits under the ceiling."""
    query = (
        f"{base} {field}:{low}..{high}"
        if high is not None
        else f"{base} {field}:>={low}"
    )
    try:
        first = _get(http, kind, query, 1)
    except OSError as error:
        log.warning("could not count %r: %s", query, error)
        return
    total = typing.cast(int, first["total_count"])
    if total == 0:
        return
    if total <= CEILING:
        yield query, first
        return
    if high is None:
        # the open end has no midpoint, so walk outwards until it closes
        middle = low * 2 if low else 8
        yield from _shards(http, kind, base, field, low, middle - 1)
        yield from _shards(http, kind, base, field, middle, None)
        return
    if low >= high:
        log.warning("%r counts %d and cannot split further", query, total)
        yield query, first
        return
    middle = low + (high - low) // 2
    yield from _shards(http, kind, base, field, low, middle)
    yield from _shards(http, kind, base, field, middle + 1, high)


def search(
    http: urllib3.PoolManager, kind: str, query: str, field: str
) -> list[str]:
    """Every repository a search names, split on `field` past the ceiling."""
    repos: list[str] = []
    for shard, first in _shards(http, kind, query, field, 0, None):
        log.info("  %s → %d", shard, first["total_count"])
        repos += _drain(http, kind, shard, first)
    return repos


def topics(
    http: urllib3.PoolManager, names: collections.abc.Iterable[str]
) -> list[str]:
    repos: list[str] = []
    for topic in names:
        log.info("topic:%s", topic)
        repos += search(http, "repositories", f"topic:{topic}", "stars")
    return repos


def code(
    http: urllib3.PoolManager, queries: collections.abc.Iterable[str]
) -> list[str]:
    repos: list[str] = []
    for query in queries:
        log.info("code: %s", query)
        repos += search(http, "code", query, "size")
    return repos
