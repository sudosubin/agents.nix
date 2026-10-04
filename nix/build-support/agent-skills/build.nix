{
  lib,
  fetchFromGitHub,
  makeSetupHook,
  rsync,
  stdenvNoCC,
  yq-go,
}:
let
  checkHook = makeSetupHook { name = "agent-skills-check-hook"; } ./check-hook.sh;
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
    nativeBuildInputs = [ rsync ] ++ lib.optional (name != null) yq-go;
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
        if [ -f "$out/SKILL.md" ]; then
          yq --inplace --front-matter=process \
            ${lib.escapeShellArg ".name = ${builtins.toJSON name}"} "$out/SKILL.md"
        fi
      ''}

      runHook postInstall
    '';

    doInstallCheck = true;
    nativeInstallCheckInputs = [ checkHook ];
  }
)
