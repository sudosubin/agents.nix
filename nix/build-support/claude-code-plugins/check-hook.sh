# shellcheck shell=bash
claudeCodePluginsCheckPhase() {
    runHook preInstallCheck

    local manifest="$out/.claude-plugin/plugin.json"
    local skill

    if [ -f "$manifest" ]; then
        if ! check-jsonschema --schemafile @schemas@/plugin-manifest.json "$manifest"; then
            echo "claude-code-plugins: ${manifest#"$out/"} does not fit the plugin schema" >&2
            exit 1
        fi
    fi

    if [ -d "$out/skills" ]; then
        shopt -s nullglob
        for skill in "$out"/skills/*/; do
            if [ -f "$skill/SKILL.md" ] && [ ! -s "$skill/SKILL.md" ]; then
                skill=${skill%/}
                echo "claude-code-plugins: skills/${skill##*/}/SKILL.md is empty" >&2
                exit 1
            fi
        done
        shopt -u nullglob
    fi

    if [ -d "$out/bin" ]; then
        echo "claude-code-plugins: bin/ ships with unpatched shebangs" >&2
    fi

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-claudeCodePluginsCheckPhase}
