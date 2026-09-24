# ci

Internal flake for the CI tasks of agents.nix. Consumers of the root flake never
evaluate anything in here. Each script states its own usage in its docstring.

## Flow

Three workflows run on their own schedules and reach each other only through
committed JSON under `data/`:

- **Fetch** scans the sites that publish skills and records which of them listed
  each repository.
- **Reconcile** asks GitHub which repositories it still serves and under what
  name. Whose repositories these are has nothing to do with who lists them, so
  this runs on its own schedule rather than waiting on a scan. A rename moves
  the whole line, rules included, and keeps the old name as an alias that warns
  when built.
- **Update** pins every repository and writes down what that revision holds.

A second kind of thing to collect writes its own scan and its own update,
because the sites that publish it and what counts as a mirrored copy are its
own. Everything after that is shared.

## How a pin is decided

Nothing about the decision is stored. Every run asks GitHub for the newest tags
and the newest commit that touched a skill, then pins in this order:

1. the highest release among the tags a hand-written `version` regex accepts,
2. the highest `vX.Y.Z` release, else the newest tag of any shape,
3. the newest commit that touched a skill directory.

A prerelease never wins: Nix orders `1.0-rc1` above `1.0`, so a package pinned
to one could never see its own release as an upgrade. Neither does an undotted
number of five digits or more, which is a date or a build number rather than a
version. A repository whose skills have run a year ahead of every tag it carries
has stopped tagging, so the first two steps are passed over until a new tag puts
it back on releases.

The version a pin carries is the one [nixpkgs asks for][versioning]: a release
names itself, and a revision that names none takes the release preceding it and
`-unstable-<date>`, or `0-unstable-<date>` when none does. A tag is taken as
immutable, so its name is what gets recorded and fetched, and an answer matching
what is already on disk is never downloaded.

[versioning]: https://github.com/NixOS/nixpkgs/blob/master/pkgs/README.md#versioning

## What is refused

Deleting is the thing worth getting wrong slowly, so a run that finds more than
5% of what it holds gone refuses the whole run. That is what a revoked token or
an outage looks like, not a real mass deletion.

Repositories that only mirror other people's skills are skipped. A revision that
is skipped, or that holds no skills at all, is still written down as an empty
snapshot: otherwise every run would find it unrecorded and ask again. Anything a
heuristic judges wrongly is settled by hand in `sources.json`, which is also
where a monorepo declares the regex that tells its packages' tags apart.

## How changes land

Sources updates land on main directly, because nothing validates them. A plain
`git push` is the compare-and-swap, so a run whose main has moved works its
answer out again rather than rebasing onto it — two answers for a generated file
cannot be merged line by line. Three collisions in a row leave it to the next
run.

A snapshot is the other way round: every skill of a changed repository is built
first, so each goes through a pull request of its own and merges once it builds.
Those are titled the way nixpkgs titles a commit — the attribute path, then what
happened to it — so `git log` reads as a package history. Everything else in the
repository, sources updates included, keeps the ordinary `feat:`/`chore:` shape:

```
agent-skills.github.anthropics.skills: init at 0-unstable-2026-09-10
agent-skills.github.vercel-labs.skills: 1.6.0 → 1.7.0
agent-skills.github.foo.bar: remove
```
