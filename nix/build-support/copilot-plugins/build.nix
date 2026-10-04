{
  lib,
  callPackage,
  check-jsonschema,
  jq,
  makeSetupHook,
}:
let
  # the manifest Copilot would load, if this plugin carries one at all
  manifests = [
    "plugin.json"
    ".plugin/plugin.json"
    ".github/plugin/plugin.json"
    ".claude-plugin/plugin.json"
  ];
in
callPackage ../build.nix { } {
  checkHook = makeSetupHook {
    name = "copilot-plugins-check-hook";
    propagatedBuildInputs = [
      check-jsonschema
      jq
    ];
    substitutions = {
      schemas = ./schemas;
      agentPlugins = ../schemas/agent-plugins;
    };
  } ./check-hook.sh;
  renameInputs = [ jq ];
  rename = name: ''
    for manifest in ${lib.escapeShellArgs manifests}; do
      if [ -f "$out/$manifest" ]; then
        tmp=$(mktemp)
        jq ${lib.escapeShellArg ".name = ${builtins.toJSON name}"} \
          "$out/$manifest" > "$tmp" && mv "$tmp" "$out/$manifest"
        break
      fi
    done
  '';
}
