# shellcheck shell=bash
codexMarketplacesCheckManifest() {
    local manifest=$1
    local file="$out/$manifest"
    local path

    if ! check-jsonschema --schemafile @schemas@/marketplace.json "$file"; then
        echo "codex-marketplaces: $manifest is not a marketplace manifest" >&2
        exit 1
    fi

    # Only Cursor manifests allow bare relative local paths.
    if [ "$manifest" != .cursor-plugin/marketplace.json ] && ! jq -e '
        all(.plugins[].source;
            if type == "string" then . == "." or startswith("./")
            elif .source == "local" then .path == "." or (.path | startswith("./"))
            else true end)' "$file" > /dev/null; then
        echo "codex-marketplaces: $manifest needs '.' or a './' prefix for local sources" >&2
        exit 1
    fi

    while IFS= read -r path; do
        path=${path#./}
        if [ ! -d "$out/${path:-.}" ]; then
            echo "codex-marketplaces: $manifest wants a missing '$path'" >&2
            exit 1
        fi
    done < <(jq -r '.plugins[] | .source
        | if type == "string" then .
          elif type == "object" and .source == "local" then .path
          else empty end' "$file")
}

codexMarketplacesCheckPhase() {
    runHook preInstallCheck

    local found=0 manifest

    # shellcheck disable=SC2043
    for manifest in @manifests@; do
        [ -f "$out/$manifest" ] || continue
        found=$((found + 1))
        codexMarketplacesCheckManifest "$manifest"
    done

    if [ "$found" -eq 0 ]; then
        echo "codex-marketplaces: $out carries none of: @manifests@" >&2
        exit 1
    fi

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-codexMarketplacesCheckPhase}
