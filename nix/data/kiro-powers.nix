{
  lib,
  callPackage,
}:
let
  buildPower = callPackage ../build-support/kiro-powers/build.nix { };
in
owner: repo: entry:
let
  powerOf =
    directory: item:
    let
      path = if directory == "" then item else "${directory}/${item}";
      name = lib.toLower (if path == "." || path == "" then repo else baseNameOf path);
      at = entry.at or { };
      pin = at.${path} or at.${directory} or entry;
    in
    lib.nameValuePair name (buildPower {
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
  lib.concatLists (lib.mapAttrsToList (directory: map (powerOf directory)) entry.paths)
)
