# Release E: markitdown-gc 0.2.0

## Decisions
- Publish only the core distribution as `markitdown-gc`, an unofficial fork.
- Keep the `markitdown` import, CLI and plugin entry-point group unchanged.
- Never install alongside upstream `markitdown`; use fresh virtual environments.
- Hatch wheel packages are explicitly `["src/markitdown"]`.
- Copy the root MIT LICENSE into the core; include it and ThirdPartyNotices.md.
- Version comes from `src/markitdown/__about__.py`: `0.2.0`.
- Use fork project URLs and fork distribution names in docs and install hints.
- Keep local directory requirements, Dockerfiles and CI paths unchanged.
- Sibling OCR/MCP/sample-plugin packages are not published for the fork yet;
  keep their identities and withhold fork installation instructions.

## Steps
1. Add a failing regression for upstream distribution names in install guidance.
2. Update core metadata, hints, READMEs and sibling publication notices.
3. Build from packages/markitdown into the external scratchpad dist directory.
4. Inspect both archives and validate strict metadata; cc handles uploading.

## Acceptance
- `python -m build --outdir <external-dist>`; `python -m twine check --strict <artifacts>`.
- List sdist/wheel contents; require GUIDE.md, LICENSE, ThirdPartyNotices.md,
  the complete markitdown package and version 0.2.0 with fork metadata/URLs.
- Fresh external Python 3.12 and 3.15 venvs: install the local wheel `[all]`.
- `pip check`; `markitdown --version`, `--guide`, `--guide install` succeed.
- `importlib.metadata.version("markitdown-gc") == "0.2.0"`; upstream absent.
- Convert synthetic HWPX from tests/_hwpx_builder.py build_hwpx(); assert text.
- Full core pytest suite with `GITHUB_ACTIONS=true` (offline gates), then Black.
- No commit, upload or token-file reads during preparation.

## Follow-up
- cc rotates/revokes any previously exposed upload token before publishing.
- Configure PyPI Trusted Publishing for the fork and retire long-lived tokens.
