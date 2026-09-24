{
  lib,
  callPackage,
}:
let
  buildSkill = callPackage ../build-support/build-skill.nix { };
in
owner: repo: entry:
let
  skillOf =
    directory: item:
    let
      path = if directory == "" then item else "${directory}/${item}";
      name = lib.toLower (if path == "." || path == "" then repo else baseNameOf path);
      at = entry.at or { };
      pin = at.${path} or at.${directory} or entry;
    in
    lib.nameValuePair name (buildSkill {
      pname = name;
      inherit
        owner
        path
        repo
        ;
      inherit (pin) rev version;
      hash = "sha256-${pin.hash}";
    });
in
builtins.listToAttrs (
  lib.concatLists (lib.mapAttrsToList (directory: map (skillOf directory)) entry.paths)
)
