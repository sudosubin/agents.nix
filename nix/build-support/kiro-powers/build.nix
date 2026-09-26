{
  lib,
  check-jsonschema,
  fetchFromGitHub,
  jq,
  makeSetupHook,
  stdenvNoCC,
  yq-go,
}:
let
  checkHook = makeSetupHook {
    name = "kiro-powers-check-hook";
    propagatedBuildInputs = [
      check-jsonschema
      jq
    ];
    substitutions.schemas = ./schemas;
  } ./check-hook.sh;
in
lib.makeOverridable (
  {
    pname,
    name ? null,
    owner,
    repo,
    rev,
    version,
    path,
    hash,
  }:
  stdenvNoCC.mkDerivation {
    inherit version;
    pname = if name == null then pname else name;

    src = fetchFromGitHub {
      inherit
        owner
        repo
        rev
        hash
        ;
    };

    sourceRoot = if path == "" || path == "." then "source" else "source/${path}";
    # a power declares its name in whichever format it uses, so both are rewritten
    nativeBuildInputs = lib.optionals (name != null) [
      jq
      yq-go
    ];
    dontBuild = true;
    dontConfigure = true;
    # Keep shipped shebangs and man pages unchanged.
    dontFixup = true;

    installPhase = ''
      runHook preInstall
      mkdir -p "$out"
      # -L would stop on a dangling link and inline whatever an absolute one hits
      find . -type l \( -lname '/*' -o -xtype l \) -delete
      cp -RL . "$out"

      ${lib.optionalString (name != null) ''
        if [ -f "$out/plugin.json" ]; then
          tmp=$(mktemp)
          jq ${lib.escapeShellArg ".name = ${builtins.toJSON name}"} \
            "$out/plugin.json" > "$tmp" && mv "$tmp" "$out/plugin.json"
        fi
        if [ -f "$out/POWER.md" ]; then
          yq --inplace --front-matter=process \
            ${lib.escapeShellArg ".name = ${builtins.toJSON name}"} "$out/POWER.md"
        fi
      ''}

      runHook postInstall
    '';

    doInstallCheck = true;
    nativeInstallCheckInputs = [ checkHook ];
  }
)
