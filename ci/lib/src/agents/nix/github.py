import json
import logging
import os
import re
import typing
from urllib.parse import urlparse

import urllib3

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


def repo_named(spec: str) -> str | None:
    """The `github:` name of an `owner/repo` spec, when it is one."""
    if re.fullmatch(r"[\w.-]+/[\w.-]+", spec):
        return f"github:{spec.lower()}"
    return None


def repo_at(url: str) -> str | None:
    """The `github:` name a GitHub URL points at, else None."""
    # git@github.com:owner/repo.git is a url everywhere but to urlparse
    if scp := re.fullmatch(r"[\w.+-]+@([\w.-]+):(.+)", url):
        url = f"ssh://{scp[1]}/{scp[2]}"
    parsed = urlparse(url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return None
    owner, _, tail = parsed.path.strip("/").partition("/")
    repo = tail.partition("/")[0].removesuffix(".git")
    return repo_named(f"{owner}/{repo}") if owner and repo else None


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
    # 5% of a small kind is nothing, so a handful is always allowed to go
    return missing > max(total * 0.05, 5)


def is_not_found(payload: Payload[typing.Any]) -> set[str | int]:
    return {
        error["path"][0]
        for error in payload.get("errors") or []
        if error.get("type") == "NOT_FOUND" and error.get("path")
    }


def graphql[T](http: urllib3.PoolManager, query: str) -> Payload[T]:
    body = json.dumps({"query": query}).encode()
    try:
        response = http.request(
            "POST",
            "https://api.github.com/graphql",
            body=body,
            retries=urllib3.Retry(
                total=5,
                backoff_factor=2.0,
                allowed_methods=None,
                status_forcelist=[403, 429, 500, 502, 503, 504],
                raise_on_status=False,
            ),
        )
    except urllib3.exceptions.HTTPError as error:
        raise OSError(f"GraphQL API unreachable: {error}") from error
    if response.status != 200:
        raise OSError(f"HTTP {response.status} from the GraphQL API")
    return typing.cast(Payload[T], json.loads(response.data))
