# Every kind reads its snapshots the same way: `paths` maps a directory to the
# items under it, and `at` pins an item or a directory to a revision of its own.
{
  lib,
  callPackage,
}:
{
  builder,
  # a rooted kind packages the directory and names the package after the item
  rooted ? false,
}:
let
  build = callPackage builder { };
in
owner: repo: entry:
let
  packageOf =
    directory: item:
    let
      key = if directory == "" then item else "${directory}/${item}";
      path =
        if !rooted then
          key
        else if directory == "" then
          "."
        else
          directory;
      name = lib.toLower (
        if rooted then
          item
        else if key == "." || key == "" then
          repo
        else
          baseNameOf key
      );
      at = entry.at or { };
      pin = at.${key} or at.${directory} or entry;
    in
    lib.nameValuePair name (build {
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
  lib.concatLists (lib.mapAttrsToList (directory: map (packageOf directory)) entry.paths)
)
