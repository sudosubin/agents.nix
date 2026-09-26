# unknown top-level fields are reported and ignored, not refused (kiro-support)
walk(
  # only additionalProperties: type, required and the name pattern still bind
  if type == "object" and .additionalProperties == false
  then del(.additionalProperties)
  else .
  end
)
