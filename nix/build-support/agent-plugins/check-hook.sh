# shellcheck shell=bash
agentPluginsValidate() {
    local file=$1 schema=$2
    if ! check-jsonschema --schemafile "@schemas@/$schema.json" "$file"; then
        echo "agent-plugins: ${file#"$out/"} does not match $schema" >&2
        exit 1
    fi
}

agentPluginsCheckPhase() {
    runHook preInstallCheck

    local manifest="$out/plugin.json"
    if [ ! -f "$manifest" ]; then
        echo "agent-plugins: $manifest is missing" >&2
        exit 1
    fi

    agentPluginsValidate "$manifest" plugin
    if [ -f "$out/mcp.json" ]; then
        local version
        version=$(jq -r '."$schema" | split("/")[-2]' "$manifest")
        agentPluginsValidate "$out/mcp.json" "mcp-$version"
    fi

    local skill marker
    shopt -s nullglob
    for skill in "$out"/skills/*/; do
        marker="${skill}SKILL.md"
        { [ -e "$marker" ] || [ -L "$marker" ]; } || continue
        if [ ! -f "$marker" ] || [ ! -s "$marker" ]; then
            echo "agent-plugins: ${marker#"$out/"} is empty or not a file" >&2
            exit 1
        fi
    done
    shopt -u nullglob

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-agentPluginsCheckPhase}
