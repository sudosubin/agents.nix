{
  lib,
  callPackage,
  check-jsonschema,
  jq,
  makeSetupHook,
  yq-go,
}:
callPackage ../build.nix { } {
  checkHook = makeSetupHook {
    name = "kiro-powers-check-hook";
    propagatedBuildInputs = [
      check-jsonschema
      jq
    ];
    substitutions.schemas = ../schemas/agent-plugins;
  } ./check-hook.sh;
  renameInputs = [
    jq
    yq-go
  ];
  rename = name: ''
    if [ -f "$out/plugin.json" ]; then
      tmp=$(mktemp)
      jq ${lib.escapeShellArg ".name = ${builtins.toJSON name}"} "$out/plugin.json" > "$tmp" \
        && mv "$tmp" "$out/plugin.json"
    fi
    # the legacy format declares the same name in POWER.md's frontmatter
    if [ -f "$out/POWER.md" ]; then
      yq --inplace --front-matter=process \
        ${lib.escapeShellArg ".name = ${builtins.toJSON name}"} "$out/POWER.md"
    fi
  '';
}
