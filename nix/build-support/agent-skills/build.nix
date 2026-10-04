{
  lib,
  callPackage,
  makeSetupHook,
  yq-go,
}:
callPackage ../build.nix { } {
  checkHook = makeSetupHook { name = "agent-skills-check-hook"; } ./check-hook.sh;
  renameInputs = [ yq-go ];
  rename = name: ''
    yq --inplace --front-matter=process \
      ${lib.escapeShellArg ".name = ${builtins.toJSON name}"} "$out/SKILL.md"
  '';
}
