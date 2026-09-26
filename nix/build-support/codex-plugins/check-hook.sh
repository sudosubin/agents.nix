# shellcheck shell=bash
# Anything without a manifest got here through a wrong `path`, not a plugin.
codexPluginsCheckPhase() {
    runHook preInstallCheck

    local manifest=""
    local candidate
    for candidate in .codex-plugin/plugin.json .claude-plugin/plugin.json; do
        if [ -f "$out/$candidate" ]; then
            manifest="$out/$candidate"
            break
        fi
    done
    if [ -z "$manifest" ]; then
        echo "codex-plugins: $out/.codex-plugin/plugin.json is missing" >&2
        exit 1
    fi
    if ! jq -e . "$manifest" > /dev/null 2>&1; then
        echo "codex-plugins: ${manifest#"$out"/} is not valid json" >&2
        exit 1
    fi

    # whatever `cp -RL` and the install phase left is a relative link
    local link target full directory
    while IFS= read -r link; do
        target=$(readlink -- "$link")
        case $target in
            /*) full=$target ;;
            *) full=${link%/*}/$target ;;
        esac
        if ! directory=$(cd -P -- "${full%/*}" 2> /dev/null && pwd); then
            echo "codex-plugins: ${link#"$out"/} points nowhere" >&2
            exit 1
        fi
        case "$directory/" in
            "$out"/*) ;;
            *)
                echo "codex-plugins: ${link#"$out"/} escapes \$out" >&2
                exit 1
                ;;
        esac
    done < <(find "$out" -type l)

    # home-manager keys a derivation on pname and never reads its manifest
    local manifest_name
    manifest_name=$(jq -r '.name // empty' "$manifest")
    if [ -z "$manifest_name" ]; then
        echo "codex-plugins: ${manifest#"$out"/} names no plugin" >&2
        exit 1
    fi
    if [ "$manifest_name" != "$pname" ]; then
        echo "codex-plugins: pname '$pname' != manifest name '$manifest_name'" >&2
        exit 1
    fi
    # codex-rs/skills/src/assets/samples/plugin-creator/scripts/identifier_validation.py
    if [[ ! $manifest_name =~ ^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$ ]]; then
        echo "codex-plugins: name '$manifest_name' is not a plugin identifier" >&2
        exit 1
    fi

    # relax.jq says which of schemastore's rules go, and why
    local relaxed
    relaxed=$(mktemp)
    jq -f @schemas@/relax.jq @schemas@/plugin-manifest.json > "$relaxed"
    if ! check-jsonschema --schemafile "$relaxed" "$manifest"; then
        echo "codex-plugins: ${manifest#"$out"/} does not match the manifest schema" >&2
        exit 1
    fi

    # a listed skill with an empty SKILL.md is one codex finds nothing in
    local skill
    shopt -s nullglob
    for skill in "$out"/skills/*/SKILL.md; do
        if [ ! -s "$skill" ]; then
            echo "codex-plugins: ${skill#"$out"/} is empty" >&2
            exit 1
        fi
    done
    shopt -u nullglob

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-codexPluginsCheckPhase}
