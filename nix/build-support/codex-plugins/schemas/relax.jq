# plugin-manifest.json is schemastore's file, byte for byte, so a fix upstream
# is picked up by downloading it again. It cannot be used as it stands: as
# published it rejects 34 of the 335 manifests this kind packages, including
# four of openai's own five official plugins.
#
# Every rejection has the same cause. The schema cites the rust manifest parser
# and validate_plugin.py, but it encodes openai's *submission checklist* — what
# a plugin needs to be listed in the official marketplace — rather than what
# codex will load. The parser has no `deny_unknown_fields`, every field is
# `#[serde(default)]`, and only `name` is not an `Option`. So the only thing it
# can actually reject is a field of the wrong JSON type.
#
# The transform therefore keeps types and structure and drops content policy:
#
#   additionalProperties  the parser ignores unknown fields, and shipped
#                         manifests already carry supportURL and brandColorDark
#   minItems / maxItems   `interface.capabilities` is `minItems: 1` while four
#                         of the five official plugins ship `[]`, and
#                         `defaultPrompt` is capped at 3 while 29 real manifests
#                         offer more. Both are `Vec<String>`; neither bound is
#                         in the rust types, and validate_plugin.py passes an
#                         empty list through `all([])`
#   minLength / pattern   openai's own data-analytics ships `"repository": ""`,
#   / format              and a plugin points privacyPolicyURL at a file in its
#                         own tree. These are `Option<String>`: blank and absent
#                         are the same thing and any string parses
#   not                   only used to spell "this path is relative"; it also
#                         has to go, because deleting the `pattern` inside it
#                         would leave `not: {}`, which rejects everything
#   required              modelled as [name, version, description, author,
#                         interface], where the runtime requires only `name`
#                         and reads `interface` as an `Option`. Narrowed rather
#                         than dropped, so the one field codex does insist on
#                         is still enforced
#
# What survives is `type`, `properties`, `items`, `oneOf`/`anyOf` and
# `required: ["name"]`. That still catches the manifests codex would choke on —
# an `author` given as a string instead of an object, `skills` given as an array
# instead of a path — and it passes a bare `{"name": "my-plugin"}`, which codex
# loads happily. Name *content* is not the schema's job here: the check hook
# asserts the 1-64 character plugin-name rule separately.
walk(
  if type == "object" then
    del(
      .additionalProperties,
      .minItems,
      .maxItems,
      .minLength,
      .pattern,
      .format,
      .not
    )
    | if has("required")
      then .required = (.required | map(select(. == "name")))
      else . end
  else . end
)
