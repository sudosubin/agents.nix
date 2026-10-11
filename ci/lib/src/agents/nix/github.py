import collections.abc
import itertools
import json
import logging
import os
import typing
from urllib.parse import quote

import urllib3

from .discovery import SearchRetry

log = logging.getLogger(__name__)


class Error(typing.TypedDict, total=False):
    type: str
    path: list[str | int]
    message: str


class Payload[T](typing.TypedDict, total=False):
    data: dict[str, T | None] | None
    errors: list[Error]


def github_token_headers() -> dict[str, str]:
    if token := os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        return urllib3.make_headers(basic_auth=f"x-access-token:{token}")
    return {}


def is_gone(http: urllib3.PoolManager, url: str) -> bool:
    try:
        response = http.request("HEAD", url, redirect=False)
    except urllib3.exceptions.HTTPError as error:
        log.warning("could not reach %s: %s", url, error)
        return False
    if response.status in {403, 429} and "Retry-After" in response.headers:
        log.warning("throttled asking about %s", url)
        return False
    return response.status in frozenset({403, 404, 410, 451})


def is_too_many_gone(missing: int, total: int) -> bool:
    # 5% of a small kind is none, so a handful may always go
    return missing > max(total * 0.05, 5)


def is_not_found(payload: Payload[typing.Any]) -> set[str | int]:
    return {
        error["path"][0]
        for error in payload.get("errors") or []
        if error.get("type") == "NOT_FOUND" and error.get("path")
    }


def graphql[T](http: urllib3.PoolManager, query: str) -> Payload[T]:
    body = json.dumps({"query": query}).encode()
    url = "https://api.github.com/graphql"
    retry = SearchRetry(
        total=5,
        backoff_factor=2.0,
        allowed_methods=None,
        status_forcelist=[403, 429, 500, 502, 503, 504],
        raise_on_status=False,
    )
    try:
        while True:
            response = http.request("POST", url, body=body, retries=retry)
            log.info(
                "GraphQL: HTTP %d, %s/%s points remaining, reset %s",
                response.status,
                response.headers.get("X-RateLimit-Remaining"),
                response.headers.get("X-RateLimit-Limit"),
                response.headers.get("X-RateLimit-Reset"),
            )
            if response.status != 200:
                raise OSError(f"HTTP {response.status} from the GraphQL API")
            payload = typing.cast(Payload[T], json.loads(response.data))
            if not any(
                error.get("type") in {"RATE_LIMIT", "RATE_LIMITED"}
                for error in payload.get("errors") or []
            ):
                return payload
            # GraphQL reports a spent quota with HTTP 200; use the same retry.
            response.status = 429
            retry = retry.increment("POST", url, response=response)
            retry.sleep(response)
    except urllib3.exceptions.HTTPError as error:
        raise OSError(f"GraphQL API unreachable: {error}") from error


TREE_FIELDS = "... on Tree { entries { name oid type mode } }"


class Entry(typing.TypedDict):
    name: str
    oid: str
    type: str
    mode: int


class Object(typing.TypedDict):
    kind: str
    oid: str
    entries: typing.NotRequired[list[Entry]]
    tree: typing.NotRequired[Object]


class Root(Object):
    rev: str


def tree_entries(
    http: urllib3.PoolManager, repo: str, oid: str, recursive: bool = True
) -> tuple[list[Entry], bool]:
    """A Git file list and whether GitHub truncated it."""
    url = f"https://api.github.com/repos/{repo}/git/trees/{oid}"
    if recursive:
        url += "?recursive=1"
    try:
        response = http.request(
            "GET",
            url,
            retries=SearchRetry(
                total=5,
                backoff_factor=2,
                status_forcelist=[403, 429, 500, 502, 503, 504],
            ),
        )
    except urllib3.exceptions.HTTPError as error:
        raise OSError(f"Git tree unavailable for {repo}: {error}") from error
    log.info(
        "REST tree %s: HTTP %d, %s/%s requests remaining, reset %s",
        repo,
        response.status,
        response.headers.get("X-RateLimit-Remaining"),
        response.headers.get("X-RateLimit-Limit"),
        response.headers.get("X-RateLimit-Reset"),
    )
    if response.status != 200:
        raise OSError(f"HTTP {response.status} reading Git tree of {repo}")
    tree = json.loads(response.data)
    if not isinstance(tree.get("tree"), list):
        raise OSError(f"Git tree entries unavailable for {repo}")
    entries = [
        Entry(
            name=entry["path"],
            oid=entry["sha"],
            type=entry["type"],
            mode=int(entry["mode"], 8),
        )
        for entry in tree["tree"]
    ]
    return entries, bool(tree.get("truncated"))


def objects(
    http: urllib3.PoolManager,
    probes: collections.abc.Iterable[tuple[str, str]],
    fields: str = "",
) -> collections.abc.Iterator[tuple[str, str, Object | None]]:
    for batch in itertools.batched(probes, 100, strict=False):
        yield from object_batch(http, batch, fields)


def object_batch(
    http: urllib3.PoolManager,
    batch: tuple[tuple[str, str], ...],
    fields: str,
) -> collections.abc.Iterator[tuple[str, str, Object | None]]:
    grouped: dict[str, list[tuple[int, str]]] = {}
    for index, (repo, expression) in enumerate(batch):
        grouped.setdefault(repo, []).append((index, expression))
    aliases = {repo: f"r{index}" for index, repo in enumerate(grouped)}
    queries = []
    for repo, expressions in grouped.items():
        owner, name = repo.split("/", 1)
        selections = " ".join(
            f"o{index}: object(expression: {json.dumps(expression)}) {{ "
            f"kind: __typename oid {fields} }}"
            for index, expression in expressions
        )
        queries.append(
            f"{aliases[repo]}: repository(owner: {json.dumps(owner)}, "
            f"name: {json.dumps(name)}) {{ {selections} }}"
        )
    try:
        payload: Payload[dict[str, Object | None]] = graphql(
            http, "{" + " ".join(queries) + "}"
        )
    except OSError as error:
        # Large directory queries can exceed GitHub's execution limit.
        if len(batch) <= 1 or not str(error).startswith("HTTP 5"):
            raise
        log.warning("splitting %d Git objects after %s", len(batch), error)
        middle = len(batch) // 2
        yield from object_batch(http, batch[:middle], fields)
        yield from object_batch(http, batch[middle:], fields)
        return
    errors = payload.get("errors") or []
    if unexpected := [e for e in errors if e.get("type") != "NOT_FOUND"]:
        raise OSError(f"Git object lookup failed: {unexpected}")
    data = payload.get("data")
    if data is None:
        raise OSError("Git object lookup returned no data")
    log.info("looked up %d Git objects", len(batch))
    for index, (repo, expression) in enumerate(batch):
        alias = aliases[repo]
        if alias not in data:
            raise OSError(f"Git object lookup omitted {repo}@{expression}")
        repository = data[alias]
        if repository is not None and f"o{index}" not in repository:
            raise OSError(f"Git object lookup omitted {repo}@{expression}")
        yield (
            repo,
            expression,
            repository[f"o{index}"] if repository else None,
        )


def root_trees(
    http: urllib3.PoolManager,
    repos: collections.abc.Iterable[str],
) -> collections.abc.Iterator[tuple[str, Root]]:
    fields = (
        "... on Commit { tree { kind: __typename oid " + TREE_FIELDS + " } }"
    )
    for batch in itertools.batched(repos, 50, strict=False):
        for repo, _, commit in objects(
            http, ((repo, "HEAD") for repo in batch), fields
        ):
            if (
                commit
                and commit.get("kind") == "Commit"
                and (tree := commit.get("tree"))
            ):
                if tree.get("entries") is None:
                    raise OSError(f"Git tree entries unavailable for {repo}")
                yield repo, typing.cast(Root, tree | {"rev": commit["oid"]})


def raw(
    http: urllib3.PoolManager,
    repo: str,
    rev: str,
    path: str,
) -> bytes | None:
    cap = 1024 * 1024
    url = (
        f"https://raw.githubusercontent.com/{repo}/{rev}/"
        f"{quote(path, safe='/')}"
    )
    try:
        response = http.request("GET", url, preload_content=False)
        try:
            if response.status != 200:
                raise OSError(f"HTTP {response.status} reading {repo}:{path}")
            blob = response.read(cap + 1)
        finally:
            response.close()
            response.release_conn()
    except urllib3.exceptions.HTTPError as error:
        raise OSError(f"could not read {repo}:{path}: {error}") from error
    return blob if len(blob) <= cap else None


def read_blob(
    http: urllib3.PoolManager,
    repo: str,
    rev: str,
    path: str,
    entry: Entry,
) -> bytes | None:
    if entry["mode"] not in {0o100644, 0o100755}:
        return None
    return raw(http, repo, rev, path)


def read_link(
    http: urllib3.PoolManager,
    repo: str,
    rev: str,
    path: str,
    entry: Entry,
) -> str | None:
    if entry["mode"] != 0o120000:
        return None
    blob = raw(http, repo, rev, path)
    if blob is None:
        return None
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        return None
