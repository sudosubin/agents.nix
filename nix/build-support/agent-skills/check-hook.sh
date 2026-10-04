# shellcheck shell=bash
agentSkillsCheckPhase() {
    runHook preInstallCheck

    if [ ! -f "$out/SKILL.md" ]; then
        echo "agent-skills: $out/SKILL.md is missing" >&2
        shopt -s nullglob
        for candidate in "$out"/[Ss][Kk][Ii][Ll][Ll].[Mm][Dd]; do
            echo "  found instead: ${candidate##*/}" >&2
        done
        shopt -u nullglob
        exit 1
    fi

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-agentSkillsCheckPhase}
