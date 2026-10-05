# shellcheck shell=bash
kiroPowersValidate() {
    local file=$1 schema=$2
    if ! check-jsonschema --schemafile "@schemas@/$schema.json" "$file"; then
        echo "kiro-powers: $file does not match $schema" >&2
        exit 1
    fi
}

kiroPowersCheckPhase() {
    runHook preInstallCheck

    if [ -f "$out/plugin.json" ]; then
        kiroPowersValidate "$out/plugin.json" plugin
        if [ -f "$out/mcp.json" ]; then
            kiroPowersValidate "$out/mcp.json" mcp
        fi
    elif [ ! -s "$out/POWER.md" ]; then
        echo "kiro-powers: $out has neither plugin.json nor a non-empty POWER.md" >&2
        exit 1
    fi

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-kiroPowersCheckPhase}
