# shellcheck shell=bash
codexMarketplacesCheckManifest() {
    local manifest=$1
    local file="$out/$manifest"
    local schema=marketplace path

    if [ "$manifest" = .cursor-plugin/marketplace.json ]; then
        schema=marketplace-cursor
    fi
    if ! check-jsonschema --schemafile "@schemas@/$schema.json" "$file"; then
        echo "codex-marketplaces: $manifest is not a marketplace manifest" >&2
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
