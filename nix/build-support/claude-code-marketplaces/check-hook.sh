# shellcheck shell=bash
claudeCodeMarketplacesCheckPhase() {
    runHook preInstallCheck

    local manifest="$out/.claude-plugin/marketplace.json"
    local name pluginRoot schema source path

    if [ ! -s "$manifest" ]; then
        echo "claude-code-marketplaces: $out/.claude-plugin/marketplace.json is missing" >&2
        exit 1
    fi

    name=$(jq --raw-output 'if (.name | type) == "string" then .name else "" end' "$manifest")
    if ! jq --exit-status --null-input --arg name "$name" '
        ($name | length) > 0 and $name != "."
        and ($name | test("^[^\u0001-\u0020\u007f-\u009f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]+$"))
        and ($name | (contains("..") or contains("/") or contains("\\")) | not)
    ' > /dev/null; then
        echo "claude-code-marketplaces: ${name:-the manifest} is not a usable name" >&2
        exit 1
    fi

    schema=$(mktemp)
    jq --from-file @schemas@/relax.jq @schemas@/marketplace.json > "$schema"
    if ! check-jsonschema --schemafile "$schema" "$manifest"; then
        echo "claude-code-marketplaces: $name does not fit the marketplace schema" >&2
        exit 1
    fi
    rm -f "$schema"

    pluginRoot=$(jq --raw-output \
        '.metadata.pluginRoot // "" | sub("^\\./"; "") | sub("/$"; "")' "$manifest")
    while IFS= read -r source; do
        case $source in
            ./*) path=${source#./} ;;
            /*) path=$source ;;
            *) path=${pluginRoot:+$pluginRoot/}$source ;;
        esac
        path=${path%/}
        if [[ $source == /* || /$path/ == */../* ]]; then
            echo "claude-code-marketplaces: $name lists $source, which is not inside it" >&2
            exit 1
        fi
        if [ ! -d "$out/${path:-.}" ]; then
            echo "claude-code-marketplaces: $name lists $source, which is not a directory" >&2
            exit 1
        fi
    done < <(jq --raw-output '.plugins[]?.source | select(type == "string")' "$manifest")

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-claudeCodeMarketplacesCheckPhase}
