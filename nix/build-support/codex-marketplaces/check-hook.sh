# shellcheck shell=bash
codexMarketplacesCheckManifest() {
    local manifest=$1
    local file="$out/$manifest"
    local name path

    if ! check-jsonschema --schemafile @schemas@/marketplace.json "$file"; then
        echo "codex-marketplaces: $manifest is not a marketplace manifest" >&2
        exit 1
    fi

    name=$(jq -r '.name' "$file")
    if [ "${#name}" -gt 64 ] || ! [[ $name =~ ^[A-Za-z0-9_-]+$ ]]; then
        echo "codex-marketplaces: $manifest calls itself '$name'" >&2
        exit 1
    fi

    while IFS= read -r path; do
        case $path in
            "" | "/"* | ".." | "../"* | *"/../"* | *"/..")
                echo "codex-marketplaces: $manifest leaves the tree at '$path'" >&2
                exit 1
                ;;
        esac
        if [ ! -d "$out/$path" ]; then
            echo "codex-marketplaces: $manifest wants a missing '$path'" >&2
            exit 1
        fi
    done < <(jq -r '.plugins[] | .source
        | select(type == "object" and .source == "local")
        | (.path // "") | sub("^\\./"; "")' "$file")
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
