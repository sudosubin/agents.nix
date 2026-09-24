{
  description = "sudosubin/agents.nix";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
  };

  outputs =
    { nixpkgs, ... }:
    let
      systems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];

      exports = import ./nix/exports.nix;
      overlay = final: prev: builtins.mapAttrs (_: make: make final prev) exports;

      forAllSystems =
        f:
        nixpkgs.lib.genAttrs systems (
          system:
          f (
            import nixpkgs {
              inherit system;
              overlays = [ overlay ];
            }
          )
        );
    in
    builtins.mapAttrs (name: _: forAllSystems (pkgs: pkgs.${name})) exports
    // {
      formatter = forAllSystems (
        pkgs:
        pkgs.nixfmt-tree.override {
          runtimeInputs = [ pkgs.ruff ];
          settings = {
            tree-root-file = "flake.nix";
            formatter.ruff = {
              command = "ruff";
              options = [ "format" ];
              includes = [ "*.py" ];
            };
          };
        }
      );

      overlays.default = overlay;
    };
}
