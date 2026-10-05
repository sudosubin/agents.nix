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
  # Native Codex catalogs; compatibility catalogs belong to their own kinds.
  manifests = [
    ".agents/plugins/marketplace.json"
    ".agents/plugins/api_marketplace.json"
  ];

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
        # Rename only the catalog selected by the runtime.
        for manifest in ${lib.escapeShellArgs manifests}; do
          [ -f "$out/$manifest" ] || continue
          tmp=$(mktemp)
          jq ${lib.escapeShellArg ".name = ${builtins.toJSON name}"} \
            "$out/$manifest" > "$tmp" && mv "$tmp" "$out/$manifest"
          break
        done
      ''}

      runHook postInstall
    '';

    doInstallCheck = true;
    nativeInstallCheckInputs = [ checkHook ];
  }
)
