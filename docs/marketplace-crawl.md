# Marketplace crawl removal review

Keep marketplace crawl for now. The existing independent scans do not cover
most repositories it contributes. Remove its snapshot bookkeeping only after
a replacement discovery path has been measured and accepted.

## Audit

Audited on 2026-10-07 at main
`ae9baff5923778fbfbfa86ba9aed5129615ddadf`. A crawl-exclusive source is live,
not skipped, and has `via` exactly `['marketplace-crawl']`. These are
kind/repository pairs, so one repository can occur in several kinds.

| Plugin kind | Active sources | Crawl-exclusive | Returned by independent scans | Not returned |
| --- | ---: | ---: | ---: | ---: |
| Claude Code | 2,469 | 1,435 | 11 | 1,424 |
| Codex | 1,277 | 1,007 | 0 | 1,007 |
| Copilot | 50 | 44 | 0 | 44 |
| Total | 3,796 | 2,486 | 11 | 2,475 |

The 2,486 exclusive pairs represent 2,365 unique repositories. The full
exclusive lists and intersections are in [the audit data](marketplace-crawl-audit.json).

The independent scans were rerun using the current fetchers:

- Claude Code: `claude-com` and `github-topics` (5,543 repositories combined).
- Codex: `github-code` and `github-topics` (1,731 repositories combined).
- Copilot: `github-topics` (47 repositories).

Names returned under an old repository name were normalized with `was` before
comparison. Claude's directory returned 218 repositories from 341 entries;
some entries name no GitHub repository. Codex's current code query returned
307 distinct repositories from 410 files.

GitHub saturated Claude topic search's 0-star and 1-star shards. Those shards
are limited to 1,000 results each. The Claude intersection is therefore an
observed result, not proof that every missing repository is undiscoverable.
Code search and the Copilot topic query were below that ceiling. Search also
reflects GitHub's current default-branch index, not the packaged revision.

Examples not returned include `github/awesome-copilot`, `github/copilot-plugins`,
`microsoft/azure-skills`, and `dotnet/skills` in Copilot, and
`anthropics/claude-plugins-official` in Codex. The existing independent scans
cannot yet replace crawl even if further Claude searches recover more results.

## Effect of stopping crawl

Stopping crawl would stop this discovery route for new repositories. It would
not immediately remove existing packages: `combine.py` accumulates sources and
provenance, and the updater continues processing existing sources. Do not
delete crawl-exclusive sources or their `via` history as part of disabling a
fetcher.

Claude and Copilot do not currently scan plugin manifests through GitHub code
search. Codex's code query searches for the text `".codex-plugin"` inside
`plugin.json`; a file at that location need not contain that text. Codex also
supports `.claude-plugin` compatibility manifests. A replacement should search
documented file locations, filter non-distribution paths, and measure the
resulting repository set against this cohort before crawl is retired.

Manifest-only discovery still needs a decision for plugins without manifests.
The Claude updater accepts directories listed by a same-repository catalog,
and its build hook validates a plugin manifest only when one exists. Dropping
catalog-based discovery could exclude packages the current build supports.

## Removing our local/remote bookkeeping later

The `entries.local` and `entries.remote` snapshot fields are derived discovery
metadata. They are read by the three plugin crawl fetchers and are not consumed
by the Nix marketplace package constructors. They can be removed after those
fetchers have a sufficient replacement or reduced coverage is explicitly chosen.

That change should:

1. Remove `fetch_marketplace_crawl` and its `FETCHERS` entries in the Claude,
   Codex, and Copilot plugin scans.
2. Remove `marketplace-crawl` from the three fetch workflow matrices and their
   marketplace-data sparse checkouts.
3. Stop emitting `entries` in all three marketplace updaters, update their
   snapshot types, and remove helpers used only to produce crawl metadata.
4. Keep mirror-detection counts and runtime source/path validation. Codex's
   `local_of` is also used to validate catalog paths; removing discovery
   bookkeeping must not remove that check. Upstream `source: local` remains
   part of the supported manifest contract.
5. Remove `entries` from the 2,756 existing marketplace snapshots in a separate
   data PR (Claude 1,302, Codex 1,443, Copilot 11). Preserve `rev`, `version`,
   `hash`, `paths`, and `at`, and confirm identical Nix derivations. Existing
   unchanged pins can bypass regeneration, so waiting for normal updates would
   leave the old fields indefinitely.

## Reproducing the comparison

At the audited checkout, run each plugin scan for the independent sites listed
above using the normal `GH_TOKEN` environment. Union each kind's
`repositories`, normalize aliases using its `sources.json`, and intersect with
its active crawl-exclusive source set. Capture search warnings as well as the
counts. Recheck the difference after changing code-search queries or adding a
new discovery route; new provenance alone is not evidence of full coverage.
