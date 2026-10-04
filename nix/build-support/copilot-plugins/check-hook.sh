# shellcheck shell=bash
copilotPluginsValidate() {
    local file=$1 schema=$2 relaxed
    relaxed=$(mktemp)
    jq --from-file "@schemas@/relax.jq" "@schemas@/$schema.json" > "$relaxed"
    if ! check-jsonschema --schemafile "$relaxed" "$file"; then
        echo "copilot-plugins: ${file#"$out"/} does not match $schema" >&2
        exit 1
    fi
}

copilotPluginsCheckPhase() {
    runHook preInstallCheck

    local skill
    shopt -s nullglob
    for skill in "$out"/skills/*/SKILL.md; do
        if [ ! -s "$skill" ]; then
            echo "copilot-plugins: ${skill#"$out"/} is empty" >&2
            exit 1
        fi
    done
    shopt -u nullglob

    local location manifest=""
    for location in plugin.json .plugin/plugin.json \
        .github/plugin/plugin.json .claude-plugin/plugin.json; do
        if [ -f "$out/$location" ]; then
            manifest="$out/$location"
            break
        fi
    done
    if [ -z "$manifest" ]; then
        runHook postInstallCheck
        return
    fi

    if ! jq -e . "$manifest" > /dev/null; then
        echo "copilot-plugins: $location is not JSON" >&2
        exit 1
    fi

    local name
    name=$(jq -r '.name // ""' "$manifest")
    if [ "${#name}" -gt 64 ] || [[ "$name" == *--* ]] || [[ "$name" == *..* ]] \
        || ! [[ "$name" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]]; then
        echo "copilot-plugins: $location names \"$name\"" >&2
        exit 1
    fi

    local version
    case "$(jq -r '."$schema" // ""' "$manifest")" in
        https://agent-plugins.org/schemas/1.0.0/plugin.schema.json) version=1.0.0 ;;
        https://agent-plugins.org/schemas/1.1.0/plugin.schema.json) version=1.1.0 ;;
        *) version="" ;;
    esac

    if [ -z "$version" ]; then
        copilotPluginsValidate "$manifest" plugin-legacy
        runHook postInstallCheck
        return
    fi

    copilotPluginsValidate "$manifest" "plugin-ap-$version"
    if [ -f "$out/mcp.json" ]; then
        copilotPluginsValidate "$out/mcp.json" "mcp-ap-$version"
    fi

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-copilotPluginsCheckPhase}
