{ callPackage }:
callPackage ../data.nix { } {
  builder = ../build-support/copilot-marketplaces/build.nix;
  rooted = true;
}
