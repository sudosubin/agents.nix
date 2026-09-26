# the snapshot closes every object, and the format outgrew it: see userConfig
walk(if type == "object" and .additionalProperties == false then del(.additionalProperties) else . end)
