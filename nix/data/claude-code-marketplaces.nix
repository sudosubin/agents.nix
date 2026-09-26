{
  lib,
  callPackage,
}:
let
  buildMarketplace = callPackage ../build-support/claude-code-marketplaces/build.nix { };
in
owner: repo: entry:
let
  # a marketplace is packaged whole, because an entry's source is a path
  # relative to the directory its manifest sits in
  marketplaceOf =
    directory: item:
    let
      name = lib.toLower item;
    in
    lib.nameValuePair name (buildMarketplace {
      pname = name;
      path = if directory == "" then "." else directory;
      inherit owner repo;
      inherit (entry) rev version;
      hash = "sha256-${entry.hash}";
    });
in
builtins.listToAttrs (
  lib.concatLists (lib.mapAttrsToList (directory: map (marketplaceOf directory)) entry.paths)
)
