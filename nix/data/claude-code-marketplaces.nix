{
  lib,
  callPackage,
}:
let
  buildMarketplace = callPackage ../build-support/claude-code-marketplaces/build.nix { };
in
owner: repo: entry:
let
  marketplaceOf =
    directory: item:
    let
      path = if directory == "" then item else "${directory}/${item}";
      name = lib.toLower item;
      at = entry.at or { };
      pin = at.${path} or at.${directory} or entry;
    in
    lib.nameValuePair name (buildMarketplace {
      pname = name;
      # the pin is keyed by `path` above; what is packaged is the root it names
      path = if directory == "" then "." else directory;
      inherit owner repo;
      inherit (pin) rev version;
      hash = "sha256-${pin.hash}";
    });
in
builtins.listToAttrs (
  lib.concatLists (lib.mapAttrsToList (directory: map (marketplaceOf directory)) entry.paths)
)
