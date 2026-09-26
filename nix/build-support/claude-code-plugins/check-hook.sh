# shellcheck shell=bash
# A Claude Code plugin has no required file: without `.claude-plugin/plugin.json`
# the loader takes whatever of the standard layout it finds, and the name comes
# from the marketplace entry. So only a manifest that exists has to hold up.
claudeCodePluginsCheckPhase() {
    runHook preInstallCheck

    local link target manifest name skill schema

    # a relative link out of the tree would resolve against the store at runtime
    while IFS= read -r link; do
        target=$(readlink -f -- "$link") || target=""
        case "$target" in
            "$out" | "$out"/*) ;;
            *)
                echo "claude-code-plugins: ${link#"$out"/} points outside the plugin" >&2
                exit 1
                ;;
        esac
    done < <(find "$out" -type l)

    manifest="$out/.claude-plugin/plugin.json"
    if [ -f "$manifest" ]; then
        # The vendored schema is a snapshot of a format that keeps growing, and
        # its closed objects reject fields Claude Code has since added -- the
        # bundled `mods/agents-md` already uses one. Everything else about the
        # schema still holds, so only the closed-ness is dropped.
        schema=$(mktemp)
        jq 'walk(
              if type == "object" and .additionalProperties == false
              then del(.additionalProperties) else . end
            )' @schemas@/plugin-manifest.json > "$schema"

        if ! check-jsonschema --schemafile "$schema" "$manifest"; then
            echo "claude-code-plugins: .claude-plugin/plugin.json is not a plugin manifest" >&2
            exit 1
        fi

        name=$(jq -r 'if has("name") then .name else "" end' "$manifest")
        if [ "${#name}" -lt 1 ] || [ "${#name}" -gt 64 ]; then
            echo "claude-code-plugins: manifest name '$name' is not 1-64 characters" >&2
            exit 1
        fi
        # the name namespaces every command and agent the plugin ships
        if [[ ! $name =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] \
            || [[ $name == *--* ]] || [[ $name == *..* ]]; then
            echo "claude-code-plugins: manifest name '$name' is not a plugin name" >&2
            exit 1
        fi
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
