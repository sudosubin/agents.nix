{ callPackage }:
callPackage ../data.nix { } { builder = ../build-support/claude-code-plugins/build.nix; }
