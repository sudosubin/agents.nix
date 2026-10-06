# Marketplace crawl removal review

Keep crawl until replacement discovery coverage is measured and accepted.
The current independent scans miss most crawl-exclusive sources.

## Coverage

Audited on 2026-10-07 at main `ae9baff5923778fbfbfa86ba9aed5129615ddadf`.
Crawl-exclusive means active, not skipped, and `via` exactly
`["marketplace-crawl"]`. Counts are kind/repository pairs.

| Plugin kind | Active | Crawl-exclusive | Rediscovered | Not returned |
| --- | ---: | ---: | ---: | ---: |
| Claude Code | 2,469 | 1,435 | 11 | 1,424 |
| Codex | 1,277 | 1,007 | 0 | 1,007 |
| Copilot | 50 | 44 | 0 | 44 |
| Total | 3,796 | 2,486 | 11 | 2,475 |

These pairs represent 2,365 unique repositories. See the
[audit data](marketplace-crawl-audit.json) for repository lists and intersections.

Reproduce at the audited checkout: run the scans below with `GH_TOKEN`, union
`repositories` by kind, normalize aliases through `sources.json`'s `was`, and
intersect with active crawl-exclusive sources. Record search warnings.

| Kind | Independent scan sites | Repositories returned |
| --- | --- | ---: |
| Claude Code | `claude-com`, `github-topics` | 5,543 |
| Codex | `github-code`, `github-topics` | 1,731 |
| Copilot | `github-topics` | 47 |

Claude's 0/1-star topic shards hit GitHub's 1,000-result limit; missing results
are not proof of undiscoverability. Other queries stayed below the limit.
Search reflects the current default-branch index, not the packaged revision.
Remeasure after changing queries; accumulated provenance cannot prove coverage.

## Replacement requirements

Stopping crawl affects new discovery; `combine.py` retains existing sources
and provenance, and updaters keep processing them.

Claude and Copilot lack plugin manifest code search. Codex searches for
`".codex-plugin"` in file contents, which can miss manifests at that path.
Replacement queries should cover supported locations, including Codex's
`.claude-plugin` compatibility manifests, exclude fixtures, and be measured
against this cohort. Claude also accepts catalog-listed directories without
manifests; manifest-only discovery needs a policy for those packages.

## Removing local/remote bookkeeping

`entries.local/remote` is snapshot discovery metadata used only by the three
crawl fetchers. Nix marketplace constructors do not consume it. Once coverage
is accepted (or reduced coverage explicitly chosen):

1. Remove crawl fetchers, `FETCHERS` entries, workflow matrix entries, and
   marketplace-data sparse checkouts from the three plugin scans.
2. Stop writing `entries` in marketplace updaters; remove its snapshot types
   and helpers used only for crawl. Keep mirror counts and source/path
   validation, including Codex's `local_of` and upstream `source: local` support.
3. In a separate data PR, remove `entries` from 2,756 snapshots (Claude 1,302,
   Codex 1,443, Copilot 11). Preserve `rev`, `version`, `hash`, `paths`, and `at`;
   verify identical derivations. Unchanged pins can bypass regeneration.
