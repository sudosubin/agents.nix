{
  lib,
  callPackage,
  check-jsonschema,
  jq,
  makeSetupHook,
}:
let
  # both have to be rewritten, since either one can be the manifest
  manifests = [
    ".codex-plugin/plugin.json"
    ".claude-plugin/plugin.json"
  ];
in
callPackage ../build.nix { } {
  checkHook = makeSetupHook {
    name = "codex-plugins-check-hook";
    propagatedBuildInputs = [
      check-jsonschema
      jq
    ];
    substitutions.schemas = ./schemas;
  } ./check-hook.sh;
  renameInputs = [ jq ];
  rename = name: ''
    for manifest in ${lib.escapeShellArgs manifests}; do
      if [ -f "$out/$manifest" ]; then
        tmp=$(mktemp)
        jq ${lib.escapeShellArg ".name = ${builtins.toJSON name}"} \
          "$out/$manifest" > "$tmp" && mv "$tmp" "$out/$manifest"
      fi
    done
  '';
}
