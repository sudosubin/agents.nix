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
      # `path` above keys the pin; the package is the root the manifest is in
      path = if directory == "" then "." else directory;
      inherit owner repo;
      inherit (pin) rev version;
      hash = "sha256-${pin.hash}";
    });
in
builtins.listToAttrs (
  lib.concatLists (lib.mapAttrsToList (directory: map (marketplaceOf directory)) entry.paths)
)
