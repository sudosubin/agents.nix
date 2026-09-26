# shellcheck shell=bash
# A packaged plugin is a directory with `.codex-plugin/plugin.json` at its root,
# or `.claude-plugin/plugin.json`, the alternate codex also accepts. Anything
# else got here through a wrong `path`, not through a plugin.
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

    # `cp -RL` resolved every link it could follow and the install phase deleted
    # the rest, so anything left is a relative link that still has to land inside
    # the closure.
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

    # home-manager's `programs.codex.plugins` takes derivations, and its
    # `canInspect` is false for one by design, so it never reads the manifest at
    # evaluation time. `pname` is what ends up in config.toml and in the
    # marketplace it synthesizes, which makes a manifest that disagrees a plugin
    # codex would look up under the wrong identity. This is what says the
    # snapshot's `names` entry reached the derivation, and what catches a plugin
    # that needed one and did not get it — a root-level plugin is named after
    # its repository, and `lib.toLower` can move a name on its own.
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
    # relax.jq drops the schema's own rules about what a string may hold, so the
    # plugin-name rule is enforced here or nowhere
    if [ "${#manifest_name}" -gt 64 ]; then
        echo "codex-plugins: name '$manifest_name' is over 64 characters" >&2
        exit 1
    fi
    if [[ ! $manifest_name =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]]; then
        echo "codex-plugins: name '$manifest_name' is not a plugin name" >&2
        exit 1
    fi
    case $manifest_name in
        *--* | *..*)
            echo "codex-plugins: name '$manifest_name' repeats a separator" >&2
            exit 1
            ;;
    esac

    # schemastore's file as published rejects a tenth of what this kind
    # packages, openai's own plugins included; relax.jq says which rules go and
    # why. What is left still catches a field of the wrong JSON type, which is
    # the only thing codex's parser can refuse.
    local relaxed
    relaxed=$(mktemp)
    jq -f @schemas@/relax.jq @schemas@/plugin-manifest.json > "$relaxed"
    if ! check-jsonschema --schemafile "$relaxed" "$manifest"; then
        echo "codex-plugins: ${manifest#"$out"/} does not match the manifest schema" >&2
        exit 1
    fi

    # `skills/` is loaded as one skill per immediate child. A child that has a
    # SKILL.md but an empty one is a skill codex would list and then find nothing
    # in; a child without one is some other payload and is left alone.
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
