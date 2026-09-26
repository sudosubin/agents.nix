# The published schemas close every object, but the clients that read these
# manifests do not: GitHub documents the same 1.0 manifest as one where
# "unknown top-level fields are reported and ignored". kirodotdev/powers ships
# an official kiro-support manifest that declares 1.0.0 and carries
# `displayName`, which the schema as published refuses — worth rechecking, in
# case upstream has since listed the field or the power has dropped it.
#
# A key every client ignores is not a reason to refuse a plugin, so only
# `additionalProperties` is lifted; what is left still says what a conformant
# client would itself refuse to load.
walk(
  if type == "object" and .additionalProperties == false then
    del(.additionalProperties)
  else
    .
  end
)
