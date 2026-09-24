{
  description = "sudosubin/agents.nix/ci";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { nixpkgs, pyproject-nix, ... }:
    let
      lib = nixpkgs.lib;
      systems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];
      nixpkgsFor = lib.genAttrs systems (system: import nixpkgs { inherit system; });
      forAllSystems = f: lib.mapAttrs (_: pkgs: f pkgs) nixpkgsFor;

      scripts =
        let
          files = lib.filterAttrs (file: type: type == "regular" && lib.hasSuffix ".py" file) (
            builtins.readDir ./scripts
          );
          load = file: pyproject-nix.lib.scripts.loadScript { script = ./scripts + "/${file}"; };
        in
        lib.mapAttrs' (file: _: lib.nameValuePair (lib.removeSuffix ".py" file) (load file)) files;

      extras =
        ps:
        let
          jsonyx = ps.callPackage ./pkgs/jsonyx.nix { };
        in
        {
          inherit jsonyx;
          agents-nix = ps.callPackage ./pkgs/agents-nix.nix { inherit jsonyx; };
        };

      mkEnv =
        pkgs: deps:
        pkgs.python315FreeThreading.withPackages (
          ps: map (dep: (extras ps).${dep.name} or ps.${dep.name}) deps
        );
    in
    {
      packages = forAllSystems (
        pkgs:
        lib.mapAttrs (
          name: script:
          pkgs.writeScriptBin name (
            "#!${mkEnv pkgs script.metadata.dependencies}/bin/python\n" + script.script
          )
        ) scripts
      );

      checks = forAllSystems (pkgs: {
        ruff = pkgs.runCommand "ruff" { nativeBuildInputs = [ pkgs.ruff ]; } ''
          ruff check --no-cache ${./.} && ruff format --no-cache --check ${./.} && touch "$out"
        '';
        # ty reads script rules from PEP 723 metadata.
        ty =
          let
            env = mkEnv pkgs (lib.concatMap (script: script.metadata.dependencies) (lib.attrValues scripts));
          in
          pkgs.runCommand "ty" { nativeBuildInputs = [ pkgs.ty ]; } ''
            ty check --python ${env} ${./scripts} ${./lib} && touch "$out"
          '';
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            pkgs.uv
            pkgs.ruff
            pkgs.ty
          ];
        };
      });
    };
}
