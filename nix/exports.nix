{
  agent-plugins =
    final: prev:
    import ./trees.nix {
      inherit (prev) lib;
      kind = "agent-plugins";
      fromRepo = prev.callPackage ./data/agent-plugins.nix { };
    };
  agent-skills =
    final: prev:
    import ./trees.nix {
      inherit (prev) lib;
      kind = "agent-skills";
      fromRepo = prev.callPackage ./data/agent-skills.nix { };
    };
  claude-code-marketplaces =
    final: prev:
    import ./trees.nix {
      inherit (prev) lib;
      kind = "claude-code-marketplaces";
      fromRepo = prev.callPackage ./data/claude-code-marketplaces.nix { };
    };
  claude-code-plugins =
    final: prev:
    import ./trees.nix {
      inherit (prev) lib;
      kind = "claude-code-plugins";
      fromRepo = prev.callPackage ./data/claude-code-plugins.nix { };
    };
  codex-marketplaces =
    final: prev:
    import ./trees.nix {
      inherit (prev) lib;
      kind = "codex-marketplaces";
      fromRepo = prev.callPackage ./data/codex-marketplaces.nix { };
    };
  codex-plugins =
    final: prev:
    import ./trees.nix {
      inherit (prev) lib;
      kind = "codex-plugins";
      fromRepo = prev.callPackage ./data/codex-plugins.nix { };
    };
  copilot-marketplaces =
    final: prev:
    import ./trees.nix {
      inherit (prev) lib;
      kind = "copilot-marketplaces";
      fromRepo = prev.callPackage ./data/copilot-marketplaces.nix { };
    };
  copilot-plugins =
    final: prev:
    import ./trees.nix {
      inherit (prev) lib;
      kind = "copilot-plugins";
      fromRepo = prev.callPackage ./data/copilot-plugins.nix { };
    };
  kiro-powers =
    final: prev:
    import ./trees.nix {
      inherit (prev) lib;
      kind = "kiro-powers";
      fromRepo = prev.callPackage ./data/kiro-powers.nix { };
    };
  skills =
    final: prev:
    prev.lib.warn "pkgs.skills is deprecated, use pkgs.agent-skills.github.<owner>.<repo>.<skill>" (
      final.agent-skills.github or { }
    );
}
