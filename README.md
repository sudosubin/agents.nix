# agents.nix

Nix expressions for AI agent skills from [skills.sh](https://skills.sh) and [skillsdirectory.com](https://www.skillsdirectory.com).

As of September 2026, this flake provides Nix derivations for over 145,000 skills sourced from more than 21,000 GitHub repositories. Each skill is individually packaged, pinned to a specific revision, and made available through a nixpkgs overlay.

## Prerequisites

### (Optional) Enable flakes

Read about [Nix flakes](https://wiki.nixos.org/wiki/Flakes) and [set them up](https://wiki.nixos.org/wiki/Flakes#Setup).

## Overlay

Read about [Overlays](https://wiki.nixos.org/wiki/Overlays#Using_overlays).

### With flakes

Add `agents.nix` to your flake inputs:

```nix
{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
    agents-nix.url = "github:sudosubin/agents.nix";
  };

  outputs = { nixpkgs, agents-nix, ... }:
    let
      pkgs = import nixpkgs {
        system = "aarch64-darwin"; # or "x86_64-linux", etc.
        overlays = [ agents-nix.overlays.default ];
      };
    in
    {
      # pkgs.agent-skills.github.<owner>.<repo>.<skill-name>
    };
}
```

### Without flakes

```nix
let
  agents-nix = import (builtins.fetchGit {
    url = "https://github.com/sudosubin/agents.nix";
    ref = "refs/heads/main";
  });

  pkgs = import <nixpkgs> {
    overlays = [ agents-nix.overlays.default ];
  };
in
  # pkgs.agent-skills.github.<owner>.<repo>.<skill-name>
```

## Usage

### Get `agent-skills`

#### Get `agent-skills` via the overlay

After applying the overlay (see [Overlay](#overlay)), skills are available under `pkgs.agent-skills`:

```nix
pkgs.agent-skills.github.<owner>.<repo>.<skill-name>
```

#### Get `agent-skills` from `agents.nix` directly

Without the overlay, you can access skills from the flake outputs:

```nix
agents-nix.agent-skills.${system}.github.<owner>.<repo>.<skill-name>
```

### Skill identifiers

Skills are organized in a four-level hierarchy: `github.<owner>.<repo>.<skill-name>`.

- `github` — the forge the repository lives on
- `owner` — repository owner, lower-cased (e.g., `vercel-labs`)
- `repo` — repository name, lower-cased (e.g., `skills`)
- `skill-name` — skill directory name (e.g., `find-skills`)

For example, a skill from the repository `vercel-labs/skills` would be accessed as:

```nix
pkgs.agent-skills.github.vercel-labs.skills.find-skills
```

> [!NOTE]
> If a skill identifier contains characters that aren't valid Nix identifiers, quote them like `pkgs.agent-skills.github."01000001-01001110"."agent-jira-skills"."jira-issues"`.

> [!IMPORTANT]
> `pkgs.skills.<owner>.<repo>.<skill-name>` still resolves and warns on evaluation. It is kept for existing configurations only.

### Example: install a skill for claude-code

```nix
# home-manager configuration
{ pkgs, ... }:

{
  programs.claude-code = {
    enable = true;
    skills = {
      find-skills = pkgs.agent-skills.github.vercel-labs.skills.find-skills;
    };
  };
}
```

### Example: install a skill for pi

```nix
# home-manager configuration
{ pkgs, ... }:

{
  home.file.".pi/agent/skills/find-skills" = {
    source = pkgs.agent-skills.github.vercel-labs.skills.find-skills;
    recursive = true;
  };
}
```

### Rename a skill

By default, `SKILL.md` is preserved unchanged. The derivation `pname` comes from the lowercased skill directory name, or the repository name for skills at the repository root.

Use `.override { name = "..."; }` to change both the derivation `pname` and the `name` field in `SKILL.md` frontmatter. Setting `name = null` restores the default package name and preserves the original file.

```nix
pkgs.agent-skills.github.vercel-labs.skills.find-skills.override { name = "my-find-skills"; }
```

## Explore

### List available skills in REPL

```console
$ nix repl

nix-repl> :lf github:sudosubin/agents.nix

nix-repl> skills = outputs.agent-skills.${builtins.currentSystem}.github

nix-repl> skills.vercel-labs.skills
{ find-skills = «derivation ...»; ... }

nix-repl> skills.vercel-labs.skills.find-skills
«derivation /nix/store/...-find-skills-4f1d38e.drv»
```

### Build a skill

```console
nix build github:sudosubin/agents.nix#agent-skills.aarch64-darwin.github.vercel-labs.skills.find-skills
```

## How it works

Three independent GitHub Actions workflows run on their own schedules and exchange data through committed JSON files in `data/`:

### Agent Skills Fetch

1. Fetches the latest skill listings from [skills.sh](https://skills.sh) and [skillsdirectory.com](https://www.skillsdirectory.com).
2. Records which of them listed each repository in `data/agent-skills/sources.json`.

### Reconcile

1. Asks GitHub for each repository's current name, and confirms the ones it no longer serves.
2. Moves a renamed repository under the name it goes by now, keeping the old one as an alias that warns when built.

### Agent Skills Update

1. Reads the committed source list (`data/agent-skills/sources.json`) to determine which repositories to process.
2. Splits the work across 16 parallel shards. For each repository, it resolves the revision to pin — the newest release, or the newest commit that touched a skill when there is no usable tag — then downloads the tarball, hashes it, and discovers all `SKILL.md` files.
3. Stores the result in `data/agent-skills/<forge>/` as one JSON file per repository, and proposes each change as its own pull request that merges once the skills build.

At evaluation time, Nix reads these JSON files and builds each skill using `fetchFromGitHub` with the pinned revision and hash. One file is read per repository, so only what you ask for is parsed.

`aarch64-darwin`, `aarch64-linux`, and `x86_64-linux` are supported. [`ci/README.md`](ci/README.md) describes the workflows in more detail.

## License

[MIT](LICENSE)
