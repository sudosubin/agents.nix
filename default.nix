# Non-flake entry point using the nixpkgs revision from flake.lock.
let
  lock = builtins.fromJSON (builtins.readFile ./flake.lock);
  locked = lock.nodes.${lock.nodes.root.inputs.nixpkgs}.locked;

  nixpkgs = {
    outPath = fetchTarball {
      url = "https://github.com/NixOS/nixpkgs/archive/${locked.rev}.tar.gz";
      sha256 = locked.narHash;
    };
    lib = import (nixpkgs.outPath + "/lib");
  };
in
(import ./flake.nix).outputs { inherit nixpkgs; }
