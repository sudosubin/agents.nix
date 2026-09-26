# shellcheck shell=bash
# A Claude Code plugin has no required file: without `.claude-plugin/plugin.json`
# the loader takes whatever of the standard layout it finds, and the name comes
# from the marketplace entry. So only a manifest that exists has to hold up.
claudeCodePluginsCheckPhase() {
    runHook preInstallCheck

    local manifest="$out/.claude-plugin/plugin.json"
    local link target name schema skill

    # a relative link out of the tree would resolve against the store at runtime
    while IFS= read -r -d '' link; do
        target=$(readlink -f -- "$link") || target=""
        case $target in
            "$out" | "$out"/*) ;;
            *)
                echo "claude-code-plugins: ${link#"$out"/} leaves the package" >&2
                exit 1
                ;;
        esac
    done < <(find "$out" -type l -print0)

    if [ -f "$manifest" ]; then
        # Claude Code's own rule, the one the marketplace kind asks of a
        # marketplace name: no space, no control or bidirectional-formatting
        # character, no path separator, no `..`, and not `.` alone. Kebab-case
        # is what `claude plugin validate` warns about, not what the loader
        # needs, so case and length are free. jq does the asking, so the
        # codepoints stay readable and the locale cannot matter.
        name=$(jq --raw-output \
            'if (.name | type) == "string" then .name else "" end' "$manifest")
        if ! jq --exit-status --null-input --arg name "$name" '
            ($name | length) > 0 and $name != "."
            and ($name | test("^[^\u0001-\u0020\u007f-\u009f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]+$"))
            and ($name | (contains("..") or contains("/") or contains("\\")) | not)
        ' > /dev/null; then
            echo "claude-code-plugins: ${name:-the manifest} is not a usable name" >&2
            exit 1
        fi

        # the vendored schema is stricter than the loader; relax.jq says where
        schema=$(mktemp)
        jq --from-file @schemas@/relax.jq @schemas@/plugin-manifest.json > "$schema"
        if ! check-jsonschema --schemafile "$schema" "$manifest"; then
            echo "claude-code-plugins: $name does not fit the plugin schema" >&2
            exit 1
        fi
        rm -f "$schema"
    fi

    if [ -d "$out/skills" ]; then
        shopt -s nullglob
        for skill in "$out"/skills/*/; do
            # a directory without SKILL.md is not a skill, and not ours to judge
            if [ -f "$skill/SKILL.md" ] && [ ! -s "$skill/SKILL.md" ]; then
                skill=${skill%/}
                echo "claude-code-plugins: skills/${skill##*/}/SKILL.md is empty" >&2
                exit 1
            fi
        done
        shopt -u nullglob
    fi

    # dontFixup keeps upstream shebangs, so these run only where they already did
    if [ -d "$out/bin" ]; then
        echo "claude-code-plugins: bin/ ships with unpatched shebangs" >&2
    fi

    runHook postInstallCheck
}

installCheckPhase=${installCheckPhase:-claudeCodePluginsCheckPhase}
