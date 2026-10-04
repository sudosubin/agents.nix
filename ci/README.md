# ci

Internal flake for the CI tasks of agents.nix. Consumers of the root flake never
evaluate anything in here.

## Flow

Three workflows run on their own schedules and reach each other only through
committed JSON under `data/<kind>/`:

- Fetch scans the sites that publish a kind, admits a new repository once it has
  10 stars, and records which sites listed each one.
- Reconcile asks GitHub which repositories it still serves and under what name. A
  rename moves the whole line, rules included, and keeps the old name as an alias
  that warns when built.
- Update pins every repository and writes down what that revision holds.

A kind is a format an agent loads: skills, or the plugins and marketplaces of one
agent. Each one owns two scripts, because the sites that publish it and the
marker that names a package in a tree are its own:

- `scripts/<kind>-scan.py` writes one scan file per site, from a registry's API
  or from a GitHub search.
- `scripts/<kind>-update.py` tells the engine in `lib` what a revision holds:
  the paths to package and, for a marketplace, what its entries point at.

Everything else is shared. `sources.yml` and `update.yml` are the workflows,
called by a `<kind>-fetch.yml` and a `<kind>-update.yml` that hold only the
schedule; `nix/data.nix` reads every kind's snapshots; `nix/build-support/build.nix`
packages a directory of a pinned revision for every kind, and runs the check
hook under `nix/build-support/<kind>/` on the result. Adding a kind is adding
those files, a `data/<kind>/sources.json`, and a `nix/data/<kind>.nix` that
names its builder.

## How a pin is decided

Nothing about the decision is stored. Every run asks GitHub for the newest tags
and the newest commit that touched a skill, then pins in this order:

1. the highest release among the tags a hand-written `version` regex accepts,
2. the highest `vX.Y.Z` release, else the newest tag of any shape,
3. the newest commit that touched a skill directory.

A prerelease never wins: Nix orders `1.0-rc1` above `1.0`, so a package pinned to
one could never see its own release as an upgrade. Neither does a name shaped like
a date, a build number, a commit or a branch. A tag whose name is a release becomes
the pin; one that is not is recorded by revision, because a name like `latest`
moves. A repository whose skills have run a year ahead of every tag it carries has
stopped tagging, so the first two steps are passed over until a new tag puts it
back on releases.

The version a pin carries is the one [nixpkgs asks for][versioning]: a release
names itself, and a revision that names none takes the release preceding it and
`-unstable-<date>`, or `0-unstable-<date>` when none does.

[versioning]: https://github.com/NixOS/nixpkgs/blob/master/pkgs/README.md#versioning

## What is refused

Deleting is the thing worth getting wrong slowly, so a run that finds more than 5%
of what it holds gone refuses the whole run. That is what a revoked token or an
outage looks like, not a real mass deletion. A handful may always go, since 5% of
a kind with a few repositories would be none.

Repositories that only mirror other people's skills are skipped. A revision that
is skipped, or that holds no skills at all, is still written down as an empty
snapshot, so no later run asks about it again. Anything a heuristic judges wrongly
is settled by hand in `sources.json`, which is also where a monorepo declares the
regex that tells its packages' tags apart. A rule that does not fit
`ci/schemas/sources.json` is refused before it lands.

## How changes land

Every commit is written through the API, which is what signs it. A required check
guards main, so a sources update goes through a pull request that auto-merge
closes. The revision it is written against is the compare-and-swap, and the branch
is cut again each run, so a request that could not merge is rebuilt.

A snapshot is the other way round: every package of a changed repository is built
first, so each goes through a pull request of its own and merges once it builds.
Those are titled the way nixpkgs titles a commit, so `git log` reads as a package
history. Everything else keeps the ordinary `feat:`/`chore:` shape:

```
agent-skills.github.anthropics.skills: init at 0-unstable-2026-09-10
agent-skills.github.vercel-labs.skills: 1.6.0 → 1.7.0
agent-skills.github.foo.bar: remove
claude-code-plugins.github.anthropics.claude-code: init at 2.1.283
```
