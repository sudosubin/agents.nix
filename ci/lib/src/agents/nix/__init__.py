import logging

import urllib3

from . import discovery as discovery
from .github import Payload as Payload
from .github import github_token_headers as github_token_headers
from .github import graphql as graphql
from .github import is_gone as is_gone
from .github import is_not_found as is_not_found
from .github import is_too_many_gone as is_too_many_gone
from .landing import NOTE as NOTE
from .landing import api as api
from .landing import blob_at as blob_at
from .landing import git as git
from .landing import open_pr as open_pr
from .landing import propose as propose
from .landing import put_file as put_file
from .landing import put_files as put_files
from .nar import archive_files as archive_files
from .nar import archive_read as archive_read
from .nar import archive_tree as archive_tree
from .nar import nar_hash as nar_hash
from .pins import Engine as Engine
from .pins import Target as Target
from .pins import pin_paths as pin_paths
from .pins import shard_of as shard_of
from .snapshots import Pin as Pin
from .snapshots import Snapshot as Snapshot
from .snapshots import Snapshots as Snapshots
from .snapshots import flatten_paths as flatten_paths
from .snapshots import group_paths as group_paths
from .sources import RULES as RULES
from .sources import Source as Source
from .sources import data_dir as data_dir
from .sources import format_sources as format_sources
from .sources import is_live as is_live


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="[%(levelname)s] %(message)s"
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def pool(
    headers: dict[str, str] | None = None,
    backoff: float = 1.0,
    maxsize: int = 1,
) -> urllib3.PoolManager:
    retry = urllib3.Retry(
        total=5,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    return urllib3.PoolManager(
        headers={"User-Agent": "agents-nix-ci"} | (headers or {}),
        retries=retry,
        timeout=120,
        maxsize=maxsize,
    )
