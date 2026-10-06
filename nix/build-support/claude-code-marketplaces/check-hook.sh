# shellcheck shell=bash
claudeCodeMarketplacesCheckPhase() {
    runHook preInstallCheck

    local manifest="$out/.claude-plugin/marketplace.json"
    local pluginRoot source path

    if [ ! -s "$manifest" ]; then
        echo "claude-code-marketplaces: $out/.claude-plugin/marketplace.json is missing" >&2
        exit 1
    fi

    if ! check-jsonschema --schemafile @schemas@/marketplace.json "$manifest"; then
        echo "claude-code-marketplaces: ${manifest#"$out/"} does not fit the marketplace schema" >&2
        exit 1
    fi

    pluginRoot=$(jq --raw-output \
        '.metadata.pluginRoot // "" | sub("^\\./"; "") | sub("/$"; "")' "$manifest")
    while IFS= read -r source; do
        case $source in
            ./*) path=${source#./} ;;
            /*) path=$source ;;
            *) path=${pluginRoot:+$pluginRoot/}$source ;;
        esac
        path=${path%/}
        if [ ! -d "$out/${path:-.}" ]; then
            echo "claude-code-marketplaces: $manifest lists $source, which is not a directory" >&2
            exit 1
        fi
    done < <(jq --raw-output '.plugins[]?.source | select(type == "string")' "$manifest")

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-claudeCodeMarketplacesCheckPhase}
