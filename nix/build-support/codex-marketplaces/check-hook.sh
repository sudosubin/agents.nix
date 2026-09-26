# shellcheck shell=bash
# A packaged marketplace is a whole repository root, because every local entry
# is a path relative to it. One root can carry several manifests, and each of
# them is a separate attribute over the same derivation source.
codexMarketplacesCheckManifest() {
    local manifest=$1
    local file="$out/$manifest"
    local name path

    if ! check-jsonschema --schemafile @schemas@/marketplace.json "$file"; then
        echo "codex-marketplaces: $manifest is not a marketplace manifest" >&2
        exit 1
    fi

    # codex-rs/skills/src/assets/samples/plugin-creator/scripts/
    # identifier_validation.py, validate_marketplace_name. It is deliberately
    # not the plugin rule beside it, which allows dots as segment separators.
    # A slash cannot pass it either, which the update side needs: group_paths
    # would read one as a directory. The length is this repository's own guard.
    name=$(jq -r '.name' "$file")
    if [ "${#name}" -gt 64 ] || ! [[ $name =~ ^[A-Za-z0-9_-]+$ ]]; then
        echo "codex-marketplaces: $manifest calls itself '$name'" >&2
        exit 1
    fi

    # a local entry codex cannot resolve is a manifest that disagrees with its
    # own tree, which is the one way this package is broken rather than merely
    # unusual; remote entries are somebody else's repository and go unchecked
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

    local found=0 link manifest target

    while IFS= read -r link; do
        target=$(readlink -f "$link" 2>/dev/null || true)
        case $target in
            "$out" | "$out"/*) ;;
            *)
                echo "codex-marketplaces: ${link#"$out"/} points out of the package" >&2
                exit 1
                ;;
        esac
    done < <(find "$out" -type l)

    # word splitting is the point: @manifests@ substitutes to the whole list
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
