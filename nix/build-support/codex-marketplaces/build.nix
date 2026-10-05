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
  # codex-rs/core-plugins/src/marketplace.rs, MARKETPLACE_MANIFEST_RELATIVE_PATHS
  manifests = [
    ".agents/plugins/marketplace.json"
    ".agents/plugins/api_marketplace.json"
    ".claude-plugin/marketplace.json"
    ".cursor-plugin/marketplace.json"
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
        # only the manifest this attribute was named after takes the new name
        for manifest in ${lib.escapeShellArgs manifests}; do
          [ -f "$out/$manifest" ] || continue
          [ "$(jq -r '(.name // "") | ascii_downcase' "$out/$manifest")" \
            = ${lib.escapeShellArg pname} ] || continue
          tmp=$(mktemp)
          jq ${lib.escapeShellArg ".name = ${builtins.toJSON name}"} \
            "$out/$manifest" > "$tmp" && mv "$tmp" "$out/$manifest"
        done
      ''}

      runHook postInstall
    '';

    doInstallCheck = true;
    nativeInstallCheckInputs = [ checkHook ];
  }
)
