# kept as downloaded, so an upstream fix lands by re-downloading it
# it is stricter than codex, whose parser only ever refuses a wrong JSON type
walk(
  if type == "object" then
    del(
      .additionalProperties, # unknown fields are ignored, not refused
      .minItems, # four of five official plugins ship `capabilities: []`
      .maxItems, # defaultPrompt is capped at three starter prompts
      .minLength, # openai's data-analytics ships `"repository": ""`
      .pattern, # a plugin points privacyPolicyURL into its own tree
      .format, # the parser does not check formats either
      .not # deleting the pattern inside would leave `not: {}`, refusing all
    )
    # every field is #[serde(default)] and only `name` is not an Option
    | if has("required")
      then .required = (.required | map(select(. == "name")))
      else . end
  else . end
)
