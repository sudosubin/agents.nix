{
  lib,
  check-jsonschema,
  fetchFromGitHub,
  jq,
  makeSetupHook,
  stdenvNoCC,
}:
let
  manifests = [
    "marketplace.json"
    ".plugin/marketplace.json"
    ".github/plugin/marketplace.json"
    ".claude-plugin/marketplace.json"
  ];
  checkHook = makeSetupHook {
    name = "copilot-marketplaces-check-hook";
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
      find . -type l \( -lname '/*' -o -xtype l -o -execdir test '{}' -ef . \; \) -delete
      cp -RL . "$out"

      ${lib.optionalString (name != null) ''
        # a repository can hold two marketplaces, so only this one is renamed
        for manifest in ${lib.escapeShellArgs manifests}; do
          [ -f "$out/$manifest" ] || continue
          tmp=$(mktemp)
          jq --arg was ${lib.escapeShellArg pname} --arg now ${lib.escapeShellArg name} \
            'if .name == $was then .name = $now else . end' \
            "$out/$manifest" > "$tmp" && mv "$tmp" "$out/$manifest"
        done
      ''}

      runHook postInstall
    '';

    doInstallCheck = true;
    nativeInstallCheckInputs = [ checkHook ];
  }
)
