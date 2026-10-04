# every object is closed, but a client ignores a key it does not know rather
# than refuse the plugin: kirodotdev/powers' kiro-support carries `displayName`
walk(
  if type == "object" and .additionalProperties == false then
    del(.additionalProperties)
  else
    .
  end
)
