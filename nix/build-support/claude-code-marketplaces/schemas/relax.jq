# marketplace.json is the schemastore snapshot as downloaded, and it is wrong
# in one place: it requires every string plugin source to start with `./`. A
# source may also be a bare directory name resolved against
# `metadata.pluginRoot`, which Claude Code has supported since v2.1.239. The
# snapshot is left untouched so a fixed one can be downloaded over it; this
# deletes that one pattern, all 24 of it, and nothing else.
walk(if type == "object" and .pattern == "^\\.\\/.*" then del(.pattern) else . end)
