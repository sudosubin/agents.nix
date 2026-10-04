{ callPackage }:
callPackage ../data.nix { } {
  builder = ../build-support/claude-code-marketplaces/build.nix;
  rooted = true;
}
