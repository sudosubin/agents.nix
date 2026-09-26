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
    # codex would look up under the wrong identity. The `lib.toLower` in the
    # naming rule is the likeliest way to get there.
    local manifest_name
    manifest_name=$(jq -r '.name // empty' "$manifest")
    if [ -n "$manifest_name" ] && [ "$manifest_name" != "$pname" ]; then
        echo "codex-plugins: pname '$pname' != manifest name '$manifest_name'" >&2
        exit 1
    fi
    if [ -n "$manifest_name" ]; then
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
    fi

    local relaxed normalised
    relaxed=$(mktemp)
    normalised=$(mktemp)
    jq -f @schemas@/relax.jq @schemas@/plugin-manifest.json > "$relaxed"
    # The same submission-checklist bias relax.jq undoes shows on the instance
    # side: `repository` is `minLength: 1` and `^https://`, yet openai's own
    # data-analytics plugin ships `"repository": ""`. Codex reads these as
    # `Option<String>`, where blank and absent are one thing, so blanks go before
    # validating. A blank `name` still fails, since `required` keeps it.
    jq 'walk(if type == "object" then with_entries(select(.value != "")) else . end)' \
        "$manifest" > "$normalised"
    if ! check-jsonschema --schemafile "$relaxed" "$normalised"; then
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
