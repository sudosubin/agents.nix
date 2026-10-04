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
  skills =
    final: prev:
    prev.lib.warn "pkgs.skills is deprecated, use pkgs.agent-skills.github.<owner>.<repo>.<skill>" (
      final.agent-skills.github or { }
    );
}
