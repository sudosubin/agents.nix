# kirodotdev/powers' kiro-support declares 1.0.0 and carries `displayName`
walk(
  if type == "object" and .additionalProperties == false then
    del(.additionalProperties)
  else
    .
  end
)
