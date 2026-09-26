# shellcheck shell=bash
# A packaged skill is a directory with SKILL.md at its root. Anything else got
# here through a wrong `path` or a case-folded checkout, not through a skill.
agentSkillsCheckPhase() {
    runHook preInstallCheck

    if [ ! -f "$out/SKILL.md" ]; then
        echo "agent-skills: $out/SKILL.md is missing" >&2
        # a case-only difference is the likeliest cause, so name what is there
        shopt -s nullglob
        for candidate in "$out"/[Ss][Kk][Ii][Ll][Ll].[Mm][Dd]; do
            echo "  found instead: ${candidate##*/}" >&2
        done
        shopt -u nullglob
        exit 1
    fi

    if [ ! -s "$out/SKILL.md" ]; then
        echo "agent-skills: $out/SKILL.md is empty" >&2
        exit 1
    fi

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-agentSkillsCheckPhase}
