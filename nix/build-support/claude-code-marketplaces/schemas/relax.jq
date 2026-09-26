# since claude code v2.1.239 a source may be bare under metadata.pluginRoot
walk(if type == "object" and .pattern == "^\\.\\/.*" then del(.pattern) else . end)
