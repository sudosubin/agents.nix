# The Agent Plugins schemas are vendored byte-for-byte, and they close every
# object. The clients do not: GitHub's Copilot documentation says of an Agent
# Plugins 1.0 manifest that unknown top-level fields are reported and ignored.
# kirodotdev/powers/kiro-support is the manifest that forced this — it declares
# 1.0.0 and carries `displayName`, which the schema as published refuses.
#
# Only additionalProperties is lifted. `type`, `required`, the name pattern and
# the mcp.json structure all describe what a conformant client refuses, so they
# stay hard failures.
walk(
  if type == "object" and .additionalProperties == false
  then del(.additionalProperties)
  else .
  end
)
