# plugin-manifest.json is the schemastore snapshot as downloaded, and it closes
# every object it describes. The format keeps growing: Anthropic's own bundled
# `mods/agents-md` already carries a `userConfig.*.options` the snapshot
# predates, which `additionalProperties: false` turns into an error for a plugin
# Claude Code loads today. The snapshot is left untouched so a fixed one can be
# downloaded over it; this opens those objects, all six of them, and nothing
# else.
walk(if type == "object" and .additionalProperties == false then del(.additionalProperties) else . end)
