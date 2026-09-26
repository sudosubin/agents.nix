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

    local link resolved
    while IFS= read -r -d '' link; do
        resolved=$(realpath -m -- "$link")
        case $resolved in
            "$out" | "$out"/*) ;;
            *)
                echo "agent-plugins: ${link#"$out/"} leaves the plugin" >&2
                exit 1
                ;;
        esac
    done < <(find "$out" -type l -print0)

    local manifest="$out/plugin.json"
    if [ ! -f "$manifest" ]; then
        echo "agent-plugins: $manifest is missing" >&2
        exit 1
    fi

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

    local name
    name=$(jq -r '.name // ""' "$manifest")
    if [ "${#name}" -lt 1 ] || [ "${#name}" -gt 64 ] \
        || [[ ! $name =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] \
        || [[ $name == *--* || $name == *..* ]]; then
        echo "agent-plugins: plugin.json names it '$name'" >&2
        exit 1
    fi

    agentPluginsValidate "$manifest" "plugin-$version"

    # the mcp schema's own `$schema` constant holds it to the same version
    if [ -f "$out/mcp.json" ]; then
        agentPluginsValidate "$out/mcp.json" "mcp-$version"
    fi

    # a client must not recurse below skills/*, so no SKILL.md means no skill
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
