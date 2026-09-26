# shellcheck shell=bash
# A plugin listed only by a marketplace entry carries no manifest of its own —
# the entry is its manifest — so nothing here fails on a missing plugin.json.
copilotPluginsCheckPhase() {
    runHook preInstallCheck

    local link resolved
    while IFS= read -r link; do
        resolved=$(readlink -f "$link" || true)
        case "$resolved" in
            "$out" | "$out"/*) ;;
            *)
                echo "copilot-plugins: ${link#"$out"/} escapes \$out" >&2
                exit 1
                ;;
        esac
    done < <(find "$out" -type l)

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

    local name
    name=$(jq -r '.name // ""' "$manifest")
    if [ "${#name}" -gt 64 ] || [[ "$name" == *--* ]] || [[ "$name" == *..* ]] \
        || ! [[ "$name" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]]; then
        echo "copilot-plugins: $location names \"$name\"" >&2
        exit 1
    fi

    # an Agent Plugins $schema opts into a closed manifest and fixed component
    # paths; anything else is the legacy format, which has no published schema
    local version
    case "$(jq -r '."$schema" // ""' "$manifest")" in
        https://agent-plugins.org/schemas/1.0.0/plugin.schema.json) version=1.0.0 ;;
        https://agent-plugins.org/schemas/1.1.0/plugin.schema.json) version=1.1.0 ;;
        *) version="" ;;
    esac

    if [ -z "$version" ]; then
        check-jsonschema --schemafile "@schemas@/plugin-legacy.json" "$manifest"
        runHook postInstallCheck
        return
    fi

    check-jsonschema --schemafile "@schemas@/plugin-ap-$version.json" "$manifest"
    if [ -f "$out/mcp.json" ]; then
        check-jsonschema --schemafile "@schemas@/mcp-ap-$version.json" "$out/mcp.json"
    fi

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-copilotPluginsCheckPhase}
