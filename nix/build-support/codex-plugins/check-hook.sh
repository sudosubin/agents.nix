# shellcheck shell=bash
codexPluginsCheckPhase() {
    runHook preInstallCheck

    local manifest=""
    local candidate
    for candidate in .codex-plugin/plugin.json .claude-plugin/plugin.json; do
        if [ -f "$out/$candidate" ]; then
            manifest="$out/$candidate"
            break
        fi
    done
    if [ -z "$manifest" ]; then
        echo "codex-plugins: $out/.codex-plugin/plugin.json is missing" >&2
        exit 1
    fi
    if ! check-jsonschema --schemafile @schemas@/plugin-manifest.json "$manifest"; then
        echo "codex-plugins: ${manifest#"$out"/} does not match the manifest schema" >&2
        exit 1
    fi

    local skill
    shopt -s nullglob
    for skill in "$out"/skills/*/SKILL.md; do
        if [ ! -s "$skill" ]; then
            echo "codex-plugins: ${skill#"$out"/} is empty" >&2
            exit 1
        fi
    done
    shopt -u nullglob

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-codexPluginsCheckPhase}
