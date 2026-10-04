{
  lib,
  callPackage,
  check-jsonschema,
  jq,
  makeSetupHook,
}:
callPackage ../build.nix { } {
  checkHook = makeSetupHook {
    name = "claude-code-marketplaces-check-hook";
    propagatedBuildInputs = [
      check-jsonschema
      jq
    ];
    substitutions.schemas = ./schemas;
  } ./check-hook.sh;
  renameInputs = [ jq ];
  rename = name: ''
    tmp=$(mktemp)
    jq ${lib.escapeShellArg ".name = ${builtins.toJSON name}"} "$out/.claude-plugin/marketplace.json" > "$tmp" \
      && mv "$tmp" "$out/.claude-plugin/marketplace.json"
  '';
}
