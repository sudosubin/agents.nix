# shellcheck shell=bash
# Kiro installs both formats alike, so plugin.json and POWER.md each name a root.

# relax.jq says why the schemas are read with their objects opened
kiroPowersValidate() {
    local file=$1 schema=$2 relaxed
    relaxed=$(mktemp)
    jq --from-file "@schemas@/relax.jq" "@schemas@/$schema.json" > "$relaxed"
    if ! check-jsonschema --schemafile "$relaxed" "$file"; then
        echo "kiro-powers: ${file#"$out/"} does not match $schema" >&2
        exit 1
    fi
}

# which spec version a document asks to be read as, empty when it says nothing
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
    local manifest="$out/plugin.json" version mcp

    if ! jq --exit-status type "$manifest" > /dev/null; then
        echo "kiro-powers: $manifest is not valid JSON" >&2
        exit 1
    fi

    version=$(kiroPowersSchemaOf "$manifest" plugin) || exit 1
    if [ -z "$version" ]; then
        echo "kiro-powers: $manifest declares no \$schema" >&2
        exit 1
    fi
    # the schema of that version also holds the name to what Kiro accepts
    kiroPowersValidate "$manifest" "plugin-$version"

    # a legacy power's mcp.json is Kiro's own format, which this schema rejects
    if [ -f "$out/mcp.json" ]; then
        mcp=$(kiroPowersSchemaOf "$out/mcp.json" mcp) || exit 1
        kiroPowersValidate "$out/mcp.json" "mcp-${mcp:-$version}"
    fi
}

kiroPowersCheckPhase() {
    runHook preInstallCheck

    # a POWER.md power has no manifest, so no schema applies and no name is required
    if [ -f "$out/plugin.json" ]; then
        kiroPowersCheckManifest
    elif [ ! -s "$out/POWER.md" ]; then
        echo "kiro-powers: $out has neither plugin.json nor a non-empty POWER.md" >&2
        exit 1
    fi

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-kiroPowersCheckPhase}
