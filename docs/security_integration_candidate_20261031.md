# Security integration candidate — 2026-10-31

This is a **Draft CI candidate only**: no new default security policy,
production release, runtime rollout or merge into `main`.

## Provenance (exact commits)

| Component | Source | Tested candidate |
| --- | --- | --- |
| MCP loopback-only HTTP/SSE binding, debug disabled | PR #28 / `78870ad7fb488f9812513076f45d84cd544c9891` | this combined branch |
| ZIP compressed/member/nested/output limits incl. nonseekable streams | PR #29 / `9d13d153ab4f6a67a911b2876cbe308af77564e2` | inherited through PR #30 |
| HTTP response byte budget and request timeout; HTTP+ZIP cross-layer regressions | PR #30 / `5b74a4e5568eee624a131b0313a3639efacae7f3` | this combined branch |

The MCP changes are exactly three file replacements from PR #28 (MCP README,
server entrypoint and negative bind tests), which are disjoint from the source
files modified by PR #30. The resulting tree must pass its **own** latest-SHA
tests; successful individual PRs are not a combined release gate.

## Explicit policy boundaries

- `markitdown-mcp --http` is unauthenticated: it must bind loopback only,
  and debug mode must remain disabled. Loopback does **not** protect against
  other untrusted local processes, malicious browser requests, or a permissive
  forwarding proxy. Authentication, origins and permissions require separate
  operational controls.
- Python `MarkItDown.convert_uri(..., http_max_bytes=..., http_timeout_seconds=...)`
  and `zip_limits=ZipLimits(...)` are **opt-in**, and are not automatically
  injected into the MCP tool. MCP callers therefore **do not yet** receive
  these conversion budgets by default. No claim of a secure untrusted MCP
  conversion service is justified.
- Resource caps do not implement SSRF or DNS/redirect filtering, PDF/OCR
  processor limits, a total processing deadline, archive member completeness
  or a process-wide memory/CPU sandbox.

## CI → review → runtime → release

1. Preserve all 24 same-head checks, incl. pre-commit, Linux, Windows,
   Windows ARM and Python version matrix, with no ignored/skipped failures.
2. Review change footprint against `main` and tests for both MCP bind
   and HTTP/ZIP resource limits on the exact integration commit.
3. Before any release, perform controlled host-specific MCP loopback tests
   plus real hostile-input and HTTP timeout/redirect/cancellation probes in
   an isolated environment; retain logs/artifacts for evidence.
4. Revalidate the **final merge commit** if the integration target changes.
5. No merge, publish, or change of production defaults is authorized here.

See `docs/resource_limits.md` for full Python API examples. Independent
security tasks and CI efficiency remain tracked in issues #26 and #31.
