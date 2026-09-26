# plugin-manifest.json is schemastore's file, byte for byte, so a fix upstream
# is picked up by downloading it again. It cannot be used as it stands: it
# rejects four of openai's own five official plugins. Three defects, all of
# them the schema modelling openai's submission checklist rather than what
# codex will load, so each one is undone here rather than patched in place.
#
#  1. `interface.capabilities` carries `minItems: 1`. Neither source the schema
#     cites asks for that — the rust field is a `Vec<String>` and
#     validate_plugin.py passes an empty list through `all([])` — and four of
#     the five official plugins ship `"capabilities": []`.
#  2. `additionalProperties: false` on the manifest and on `interface`, while
#     the parser has no `deny_unknown_fields` and every field is
#     `#[serde(default)]`. Shipped manifests already carry `supportURL` and
#     `brandColorDark`, which the schema does not list.
#  3. `required: [name, version, description, author, interface]`, where the
#     runtime requires only `name` and reads `interface` as an `Option`.
#
# Deleting only the first two still fails a bare `{"name": "my-plugin"}` on
# version/description/author, which codex loads happily. So `required` is
# narrowed to `name` everywhere rather than dropped, which keeps the one
# constraint the runtime does enforce.
walk(
  if type == "object" then
    del(.additionalProperties)
    | del(.minItems)
    | if has("required")
      then .required = (.required | map(select(. == "name")))
      else . end
  else . end
)
