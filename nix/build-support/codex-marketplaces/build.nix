{
  lib,
  callPackage,
  check-jsonschema,
  jq,
  makeSetupHook,
}:
let
  # codex-rs/core-plugins/src/marketplace.rs, MARKETPLACE_MANIFEST_RELATIVE_PATHS
  # cursor gets no kind of its own; codex's own list is why its path is here
  manifests = [
    ".agents/plugins/marketplace.json"
    ".agents/plugins/api_marketplace.json"
    ".claude-plugin/marketplace.json"
    ".cursor-plugin/marketplace.json"
  ];
in
callPackage ../build.nix { } {
  checkHook = makeSetupHook {
    name = "codex-marketplaces-check-hook";
    propagatedBuildInputs = [
      check-jsonschema
      jq
    ];
    substitutions = {
      manifests = lib.concatStringsSep " " manifests;
      schemas = ./schemas;
    };
  } ./check-hook.sh;
  renameInputs = [ jq ];
  rename = name: ''
    # only the manifest this attribute was named after takes the new name
    for manifest in ${lib.escapeShellArgs manifests}; do
      [ -f "$out/$manifest" ] || continue
      [ "$(jq -r '(.name // "") | ascii_downcase' "$out/$manifest")" = "$pname" ] || continue
      tmp=$(mktemp)
      jq ${lib.escapeShellArg ".name = ${builtins.toJSON name}"} \
        "$out/$manifest" > "$tmp" && mv "$tmp" "$out/$manifest"
    done
  '';
}
