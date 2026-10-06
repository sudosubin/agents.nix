# shellcheck shell=bash
kiroPowersCheckPhase() {
    runHook preInstallCheck

    if [ ! -s "$out/POWER.md" ]; then
        echo "kiro-powers: $out has no non-empty POWER.md" >&2
        exit 1
    fi

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-kiroPowersCheckPhase}
