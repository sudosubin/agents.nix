import logging

import urllib3

from . import discovery as discovery
from .github import Payload as Payload
from .github import github_token_headers as github_token_headers
from .github import graphql as graphql
from .github import is_gone as is_gone
from .github import is_not_found as is_not_found
from .github import is_too_many_gone as is_too_many_gone
from .github import repo_at as repo_at
from .github import repo_named as repo_named
from .landing import NOTE as NOTE
from .landing import api as api
from .landing import blob_at as blob_at
from .landing import delete_file as delete_file
from .landing import git as git
from .landing import open_pr as open_pr
from .landing import propose as propose
from .landing import put_file as put_file
from .layout import BUILD_DIRS as BUILD_DIRS
from .layout import VENDORED_DIRS as VENDORED_DIRS
from .layout import depth as depth
from .layout import directories as directories
from .layout import is_manifest_dir as is_manifest_dir
from .layout import is_mirror as is_mirror
from .layout import is_vendored as is_vendored
from .layout import outermost as outermost
from .layout import select_canonical as select_canonical
from .nar import archive_files as archive_files
from .nar import archive_read as archive_read
from .nar import archive_tree as archive_tree
from .nar import nar_hash as nar_hash
from .pins import Engine as Engine
from .pins import Packaged as Packaged
from .pins import Target as Target
from .pins import shard_of as shard_of
from .snapshots import Pin as Pin
from .snapshots import Snapshot as Snapshot
from .snapshots import Snapshots as Snapshots
from .snapshots import flatten_paths as flatten_paths
from .snapshots import group_paths as group_paths
from .sources import RULES as RULES
from .sources import Source as Source
from .sources import data_dir as data_dir
from .sources import is_live as is_live
from .sources import listed_by as listed_by
from .sources import read_sources as read_sources
from .sources import write_scan as write_scan
from .sources import write_sources as write_sources


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
