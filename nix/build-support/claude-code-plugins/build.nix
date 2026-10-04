{
  lib,
  callPackage,
  check-jsonschema,
  jq,
  makeSetupHook,
}:
callPackage ../build.nix { } {
  checkHook = makeSetupHook {
    name = "claude-code-plugins-check-hook";
    propagatedBuildInputs = [
      check-jsonschema
      jq
    ];
    substitutions.schemas = ./schemas;
  } ./check-hook.sh;
  renameInputs = [ jq ];
  rename = name: ''
    if [ -f "$out/.claude-plugin/plugin.json" ]; then
      tmp=$(mktemp)
      jq ${lib.escapeShellArg ".name = ${builtins.toJSON name}"} "$out/.claude-plugin/plugin.json" > "$tmp" \
        && mv "$tmp" "$out/.claude-plugin/plugin.json"
    fi
  '';
}
