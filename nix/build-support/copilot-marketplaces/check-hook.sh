# shellcheck shell=bash
# a marketplace loads nothing if a relative source did not come along with it
copilotMarketplacesCheckPhase() {
    runHook preInstallCheck

    # only the manifest naming this package, since a repository can hold two
    local manifests=() candidate found
    for candidate in marketplace.json .plugin/marketplace.json \
        .github/plugin/marketplace.json .claude-plugin/marketplace.json; do
        [ -f "$out/$candidate" ] || continue
        found=$(jq -r '.name // ""' "$out/$candidate" 2>/dev/null) || found=
        if [ "$found" = "$pname" ]; then
            manifests+=("$candidate")
        fi
    done

    if [ "${#manifests[@]}" -eq 0 ]; then
        echo "copilot-marketplaces: no manifest under $out names '$pname'" >&2
        exit 1
    fi

    if [ "${#pname}" -gt 64 ] || [[ $pname == *--* || $pname == *..* ]] ||
        [[ ! $pname =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]]; then
        echo "copilot-marketplaces: '$pname' is not a usable marketplace name" >&2
        exit 1
    fi

    local manifest root entry source path
    for manifest in "${manifests[@]}"; do
        if ! check-jsonschema --schemafile @schemas@/marketplace.json \
            "$out/$manifest"; then
            echo "copilot-marketplaces: $manifest is not a marketplace" >&2
            exit 1
        fi

        root=$(jq -r '.metadata.pluginRoot // ""' "$out/$manifest")
        root=${root#./}
        root=${root%/}
        while IFS=$'\t' read -r entry source; do
            path=${source#./}
            if [ -n "$root" ]; then  # only a bare name resolves against it
                case $path in
                    */*) ;;
                    *) path="$root/$path" ;;
                esac
            fi
            if [[ $path == /* || /$path/ == */../* ]]; then
                echo "copilot-marketplaces: $manifest entry '$entry' leaves the" \
                    "marketplace: '$source'" >&2
                exit 1
            fi
            if [ ! -d "$out/$path" ]; then
                echo "copilot-marketplaces: $manifest entry '$entry' has no" \
                    "directory '$path'" >&2
                exit 1
            fi
        done < <(jq -r '.plugins[]?
            | select((.source | type) == "string")
            | select((.source | test("://")) | not)
            | [.name, .source] | @tsv' "$out/$manifest")
    done

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-copilotMarketplacesCheckPhase}
