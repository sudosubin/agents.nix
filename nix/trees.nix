{
  lib,
  kind,
  fromRepo,
}:
let
  root = ../data + "/${kind}";
  forges = builtins.fromJSON (builtins.readFile ./forges.json);
  readJSON = file: builtins.fromJSON (builtins.readFile file);
  subdirectories = dir: lib.filterAttrs (_: type: type == "directory") (builtins.readDir dir);

  reposIn =
    dir:
    let
      repoOf =
        owner: file: _:
        let
          repo = lib.removeSuffix ".json" file;
        in
        lib.nameValuePair repo (fromRepo owner repo (readJSON (dir + "/${owner}/${file}")));
      reposOf = owner: _: lib.mapAttrs' (repoOf owner) (builtins.readDir (dir + "/${owner}"));
    in
    lib.mapAttrs reposOf (builtins.readDir dir);

  sourcesFile = root + "/sources.json";
  sources = lib.optionalAttrs (builtins.pathExists sourcesFile) (readJSON sourcesFile);
  pathOf =
    name:
    let
      parts = lib.splitString ":" name;
    in
    [ (lib.head parts) ] ++ lib.splitString "/" (lib.last parts);
  renames = lib.concatLists (
    lib.mapAttrsToList (
      name: source:
      map (old: {
        from = pathOf old;
        to = pathOf name;
      }) (source.was or [ ])
    ) sources
  );
  attrPath = path: lib.concatStringsSep "." ([ kind ] ++ path);

  aliasesIn =
    forge: repos:
    let
      within = rename: lib.head rename.from == forge && lib.head rename.to == forge;
      aliasOf =
        rename:
        let
          target = lib.attrByPath (lib.tail rename.to) null repos;
          warn = lib.warnOnInstantiate "${attrPath rename.from} was renamed to ${attrPath rename.to}";
        in
        lib.optional (target != null) (
          lib.nameValuePair (lib.last rename.from) (lib.mapAttrs (_: warn) target)
        );
      byOwner = lib.groupBy (rename: lib.elemAt rename.from 1) (lib.filter within renames);
    in
    lib.mapAttrs (_: owned: lib.listToAttrs (lib.concatMap aliasOf owned)) byOwner;

  forgeIn =
    forge: dir:
    let
      repos = reposIn dir;
      aliased = aliasesIn forge repos;
      ownerOf = owner: _: (aliased.${owner} or { }) // (repos.${owner} or { });
    in
    lib.mapAttrs ownerOf (aliased // repos);

  forgeOf =
    dir: _:
    let
      name = forges.${dir} or dir;
    in
    lib.nameValuePair name (forgeIn name (root + "/${dir}"));
in
lib.mapAttrs' forgeOf (subdirectories root)
