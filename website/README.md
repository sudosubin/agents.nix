# website

The agents.nix package catalog, rendered from `data/` with Python, Jinja2,
Pagefind search, and Tailwind CSS.

## Build and preview

From the repository root:

```console
$ nix shell nixpkgs#uv
$ uv run --script website/scripts/build.py --base /agents.nix/ --out website/dist/agents.nix
$ python3 -m http.server 8765 --bind 127.0.0.1 --directory website/dist
```

Open <http://127.0.0.1:8765/agents.nix/>. The default build uses `/` as its URL
prefix and writes to `website/dist`.

For layout changes, add `--no-index` to reuse the existing search index. Keep
the same `--base` and `--out`; rebuild fully after data or indexing changes.

Dependencies are declared in `scripts/build.py` and installed by uv.

CSS and JS sources stay in `templates/base.html`; the build writes shared
assets. Search uses Pagefind Component UI with custom styles and no Pagefind CSS.

## Checks

```console
$ uvx ruff@0.16.8 check website
$ uvx ruff@0.16.8 format --check website
$ uv sync --script website/scripts/build.py
$ "$(uv python find --script website/scripts/build.py)" -m unittest discover --start-directory website/scripts
```

After template changes, check search, kind filtering, clearing, result links,
both themes, and narrow screens in a browser.

## Deployment

GitHub Actions builds and publishes to GitHub Pages daily, on manual dispatch,
and when website changes reach `main`. Pages must use GitHub Actions as its
source. The Validate workflow checks changes without publishing.
