{
  lib,
  callPackage,
  check-jsonschema,
  jq,
  makeSetupHook,
}:
let
  manifests = [
    "marketplace.json"
    ".plugin/marketplace.json"
    ".github/plugin/marketplace.json"
    ".claude-plugin/marketplace.json"
  ];
in
callPackage ../build.nix { } {
  checkHook = makeSetupHook {
    name = "copilot-marketplaces-check-hook";
    propagatedBuildInputs = [
      check-jsonschema
      jq
    ];
    substitutions.schemas = ./schemas;
  } ./check-hook.sh;
  renameInputs = [ jq ];
  rename = name: ''
    # a repository can hold two marketplaces, so only this one is renamed
    for manifest in ${lib.escapeShellArgs manifests}; do
      [ -f "$out/$manifest" ] || continue
      tmp=$(mktemp)
      jq --arg was "$pname" --arg now ${lib.escapeShellArg name} \
        'if .name == $was then .name = $now else . end' \
        "$out/$manifest" > "$tmp" && mv "$tmp" "$out/$manifest"
    done
  '';
}
