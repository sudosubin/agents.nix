{
  agent-skills =
    final: prev:
    import ./trees.nix {
      inherit (prev) lib;
      kind = "agent-skills";
      fromRepo = prev.callPackage ./data/agent-skills.nix { };
    };
  skills =
    final: prev:
    prev.lib.warn "pkgs.skills is deprecated, use pkgs.agent-skills.github.<owner>.<repo>.<skill>" (
      final.agent-skills.github or { }
    );
}
