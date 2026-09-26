{
  lib,
  check-jsonschema,
  fetchFromGitHub,
  jq,
  makeSetupHook,
  stdenvNoCC,
}:
let
  checkHook = makeSetupHook {
    name = "copilot-plugins-check-hook";
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
    nativeBuildInputs = lib.optional (name != null) jq;
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
        # the manifest Copilot would load, if this plugin carries one at all
        for manifest in plugin.json .plugin/plugin.json \
          .github/plugin/plugin.json .claude-plugin/plugin.json; do
          if [ -f "$out/$manifest" ]; then
            tmp=$(mktemp)
            jq ${lib.escapeShellArg ".name = ${builtins.toJSON name}"} \
              "$out/$manifest" > "$tmp" && mv "$tmp" "$out/$manifest"
            break
          fi
        done
      ''}

      runHook postInstall
    '';

    doInstallCheck = true;
    nativeInstallCheckInputs = [ checkHook ];
  }
)
