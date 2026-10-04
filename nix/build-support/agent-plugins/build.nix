{
  lib,
  callPackage,
  check-jsonschema,
  jq,
  makeSetupHook,
}:
callPackage ../build.nix { } {
  checkHook = makeSetupHook {
    name = "agent-plugins-check-hook";
    propagatedBuildInputs = [
      check-jsonschema
      jq
    ];
    substitutions.schemas = ../schemas/agent-plugins;
  } ./check-hook.sh;
  renameInputs = [ jq ];
  rename = name: ''
    tmp=$(mktemp)
    jq ${lib.escapeShellArg ".name = ${builtins.toJSON name}"} "$out/plugin.json" > "$tmp" \
      && mv "$tmp" "$out/plugin.json"
  '';
}
