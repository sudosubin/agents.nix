# Every kind packages one directory of a pinned GitHub revision this way. What
# differs is the check that runs on the result, and how a renamed package tells
# its manifest.
{
  lib,
  fetchFromGitHub,
  stdenvNoCC,
}:
{
  checkHook,
  # a shell snippet rewriting the name a manifest under $out declares
  rename ? (_: ""),
  renameInputs ? [ ],
}:
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
    nativeBuildInputs = lib.optionals (name != null) renameInputs;
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
      ${lib.optionalString (name != null) (rename name)}
      runHook postInstall
    '';

    doInstallCheck = true;
    nativeInstallCheckInputs = [ checkHook ];
  }
)
