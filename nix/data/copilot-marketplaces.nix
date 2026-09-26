{
  lib,
  callPackage,
}:
let
  buildMarketplace = callPackage ../build-support/copilot-marketplaces/build.nix { };
in
owner: repo: entry:
let
  # a marketplace is named by its manifest, and packaged as the tree its
  # entries are relative to, so the item is the name and the directory the path
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
      # the package is the marketplace root, because an entry's source is
      # relative to it; `path` above is only the key the engine pins under
      path = if directory == "" then "." else directory;
      inherit owner repo;
      inherit (pin) rev version;
      hash = "sha256-${pin.hash}";
    });
in
builtins.listToAttrs (
  lib.concatLists (lib.mapAttrsToList (directory: map (marketplaceOf directory)) entry.paths)
)
