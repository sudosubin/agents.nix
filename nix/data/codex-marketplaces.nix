{
  lib,
  callPackage,
}:
let
  buildMarketplace = callPackage ../build-support/codex-marketplaces/build.nix { };
in
owner: repo: entry:
let
  # the item is the marketplace's own name and the directory is what gets
  # packaged, so two manifests over one root are two attributes, one source
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
