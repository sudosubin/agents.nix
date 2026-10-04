{ callPackage }:
callPackage ../data.nix { } {
  builder = ../build-support/codex-marketplaces/build.nix;
  rooted = true;
}
