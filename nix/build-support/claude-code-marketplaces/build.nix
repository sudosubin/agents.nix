{
  lib,
  check-jsonschema,
  fetchFromGitHub,
  jq,
  makeSetupHook,
  rsync,
  stdenvNoCC,
}:
let
  checkHook = makeSetupHook {
    name = "claude-code-marketplaces-check-hook";
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
    nativeBuildInputs = [ rsync ] ++ lib.optional (name != null) jq;
    dontBuild = true;
    dontConfigure = true;
    # Keep shipped shebangs and man pages unchanged.
    dontFixup = true;

    installPhase = ''
      runHook preInstall
      mkdir -p "$out"
      # Drop absolute and dangling links before copying external targets.
      find . -type l \( -lname '/*' -o -xtype l \) -delete
      rsync -rlpt --copy-unsafe-links ./ "$out/"

      ${lib.optionalString (name != null) ''
        if [ -f "$out/.claude-plugin/marketplace.json" ]; then
          tmp=$(mktemp)
          jq ${lib.escapeShellArg ".name = ${builtins.toJSON name}"} \
            "$out/.claude-plugin/marketplace.json" > "$tmp" &&
            mv "$tmp" "$out/.claude-plugin/marketplace.json"
        fi
      ''}

      runHook postInstall
    '';

    doInstallCheck = true;
    nativeInstallCheckInputs = [ checkHook ];
  }
)
