{ callPackage }:
callPackage ../data.nix { } { builder = ../build-support/copilot-plugins/build.nix; }
