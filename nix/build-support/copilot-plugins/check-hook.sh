# shellcheck shell=bash
# a plugin listed only by a marketplace entry has no manifest of its own

# relax.jq says why; it is a no-op on plugin-legacy.json, which closes nothing
copilotPluginsValidate() {
    local file=$1 schema=$2 relaxed
    relaxed=$(mktemp)
    jq --from-file "@agentPlugins@/relax.jq" "$schema" > "$relaxed"
    if ! check-jsonschema --schemafile "$relaxed" "$file"; then
        echo "copilot-plugins: ${file#"$out"/} does not match ${schema##*/}" >&2
        exit 1
    fi
}

copilotPluginsCheckPhase() {
    runHook preInstallCheck

    # skills/ is the one component laid out the same way in both formats
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

    # an Agent Plugins $schema fixes where the components live; nothing else does
    local version
    case "$(jq -r '."$schema" // ""' "$manifest")" in
        https://agent-plugins.org/schemas/1.0.0/plugin.schema.json) version=1.0.0 ;;
        https://agent-plugins.org/schemas/1.1.0/plugin.schema.json) version=1.1.0 ;;
        *) version="" ;;
    esac

    if [ -z "$version" ]; then
        copilotPluginsValidate "$manifest" "@schemas@/plugin-legacy.json"
        runHook postInstallCheck
        return
    fi

    # the schema of that version also holds the name to what Copilot accepts
    copilotPluginsValidate "$manifest" "@agentPlugins@/plugin-$version.json"
    if [ -f "$out/mcp.json" ]; then
        copilotPluginsValidate "$out/mcp.json" "@agentPlugins@/mcp-$version.json"
    fi

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-copilotPluginsCheckPhase}
