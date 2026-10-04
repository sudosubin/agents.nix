# shellcheck shell=bash
# a root plugin.json is the marker only with an Agent Plugins `$schema` on it

# relax.jq says why the schemas are read with their objects opened
agentPluginsValidate() {
    local file=$1 schema=$2 relaxed
    relaxed=$(mktemp)
    jq --from-file "@schemas@/relax.jq" "@schemas@/$schema.json" > "$relaxed"
    if ! check-jsonschema --schemafile "$relaxed" "$file"; then
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

    # the schema of that version also holds the name to what a client accepts
    local spec version
    spec=$(jq -r '.["$schema"] // ""' "$manifest")
    case $spec in
        https://agent-plugins.org/schemas/1.0.0/plugin.schema.json) version=1.0.0 ;;
        https://agent-plugins.org/schemas/1.1.0/plugin.schema.json) version=1.1.0 ;;
        *)
            echo "agent-plugins: unknown \$schema: ${spec:-none}" >&2
            exit 1
            ;;
    esac
    agentPluginsValidate "$manifest" "plugin-$version"

    # the mcp schema's own `$schema` constant holds it to the same version
    if [ -f "$out/mcp.json" ]; then
        agentPluginsValidate "$out/mcp.json" "mcp-$version"
    fi

    # a client must not recurse below skills/*, so an empty SKILL.md is no skill
    local marker
    shopt -s nullglob
    for marker in "$out"/skills/*/SKILL.md; do
        if [ ! -s "$marker" ]; then
            echo "agent-plugins: ${marker#"$out/"} is empty" >&2
            exit 1
        fi
    done
    shopt -u nullglob

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-agentPluginsCheckPhase}
