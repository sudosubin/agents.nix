# shellcheck shell=bash
kiroPowersValidate() {
    local file=$1 schema=$2 relaxed
    relaxed=$(mktemp)
    jq 'walk(if type == "object" and .additionalProperties == false
             then del(.additionalProperties) else . end)' \
        "@schemas@/$schema.json" > "$relaxed"
    if ! check-jsonschema --schemafile "$relaxed" "$file"; then
        echo "kiro-powers: $file does not match $schema" >&2
        exit 1
    fi
}

kiroPowersSchemaOf() {
    local declared
    declared=$(jq --raw-output '."$schema" // ""' "$1")
    case "$declared" in
        "https://agent-plugins.org/schemas/1.0.0/$2.schema.json") echo 1.0.0 ;;
        "https://agent-plugins.org/schemas/1.1.0/$2.schema.json") echo 1.1.0 ;;
        "") echo "" ;;
        *)
            echo "kiro-powers: $1 declares an unknown \$schema: $declared" >&2
            exit 1
            ;;
    esac
}

kiroPowersCheckManifest() {
    local manifest="$out/plugin.json" version name mcp

    if ! jq --exit-status type "$manifest" > /dev/null; then
        echo "kiro-powers: $manifest is not valid JSON" >&2
        exit 1
    fi

    version=$(kiroPowersSchemaOf "$manifest" plugin) || exit 1
    if [ -z "$version" ]; then
        echo "kiro-powers: $manifest declares no \$schema" >&2
        exit 1
    fi

    name=$(jq --raw-output '.name // ""' "$manifest")
    if [ "${#name}" -lt 1 ] || [ "${#name}" -gt 64 ]; then
        echo "kiro-powers: name must be 1-64 characters, got ${#name}" >&2
        exit 1
    fi
    if [[ ! $name =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] ||
        [[ $name == *--* || $name == *..* ]]; then
        echo "kiro-powers: name is not a plugin name: $name" >&2
        exit 1
    fi

    kiroPowersValidate "$manifest" "plugin-$version"

    if [ -f "$out/mcp.json" ]; then
        mcp=$(kiroPowersSchemaOf "$out/mcp.json" mcp) || exit 1
        kiroPowersValidate "$out/mcp.json" "mcp-${mcp:-$version}"
    fi
}

kiroPowersCheckPhase() {
    runHook preInstallCheck

    if [ -f "$out/plugin.json" ]; then
        kiroPowersCheckManifest
    elif [ ! -s "$out/POWER.md" ]; then
        echo "kiro-powers: $out has neither plugin.json nor a non-empty POWER.md" >&2
        exit 1
    fi

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-kiroPowersCheckPhase}
