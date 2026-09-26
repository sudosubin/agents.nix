# a kind is a `./data/<kind>.nix`, so adding one is a new file rather than an edit here
let
  files = builtins.readDir ./data;
  isBuilder = name: files.${name} == "regular" && builtins.match ".*\\.nix" name != null;
  kindOf = name: builtins.substring 0 (builtins.stringLength name - 4) name;
  kinds = map kindOf (builtins.filter isBuilder (builtins.attrNames files));

  export = kind: {
    name = kind;
    value =
      final: prev:
      import ./trees.nix {
        inherit (prev) lib;
        inherit kind;
        fromRepo = prev.callPackage (./data + "/${kind}.nix") { };
      };
  };
in
builtins.listToAttrs (map export kinds)
// {
  skills =
    final: prev:
    prev.lib.warn "pkgs.skills is deprecated, use pkgs.agent-skills.github.<owner>.<repo>.<skill>" (
      final.agent-skills.github or { }
    );
}
