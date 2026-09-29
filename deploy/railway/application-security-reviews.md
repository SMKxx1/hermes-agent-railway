# Application source advisory review — 2026-09-29

The editable `hermes-agent` application declares version `0.0.0`. Version-based
alerts therefore require source review: neither changing that placeholder nor a
clean third-party dependency audit proves that an application advisory is fixed.
Keep vulnerability alerts enabled and review new or changed records individually.

## Existing fixes verified in this distribution

The following functional fixes are ancestors of public revision
`0e4c1eaec3e49cad4b9ff2ea1a5336dcc8a6f0e8`. Their current implementations were
inspected after refactors, and the reported behavior was checked with regressions.
The active Railway CI lane now retains these tests across upstream synchronization.

| Advisory | Functional fix | Source behavior retained |
| --- | --- | --- |
| [GHSA-4pqm-j46f-795x](https://github.com/advisories/GHSA-4pqm-j46f-795x) | `2e66eefbc3251067f83a13d0a32ca51524f4f9a2` | All four affected WebSocket routes check Host/Origin before acceptance. |
| [GHSA-99f9-j8r3-p853](https://github.com/advisories/GHSA-99f9-j8r3-p853) | `3bace071bfadf2d2bec2ee048471a31ec920e3e8` | ResponseStore DB/WAL/SHM and webhook subscription files are owner-only. |
| [GHSA-238w-f66p-w349](https://github.com/advisories/GHSA-238w-f66p-w349) | `7ebebfbb8d10937cbf2219b4cf5e121b71e67428` | Skills Guard checks multiword prompt/policy overrides. |
| [GHSA-pgp4-xr4j-h5cg](https://github.com/advisories/GHSA-pgp4-xr4j-h5cg) | `0dee92df22bdc0cfbcad90ca954aa14916f018de` | Project context scanning uses the shared threat patterns with context scope. |
| [GHSA-wm96-9gfh-vvgq](https://github.com/advisories/GHSA-wm96-9gfh-vvgq) | `1083977261ec96a3234851c74f2dada0eec20518` | The execution guard precedes local and remote execution; approval context propagates; child environment filtering excludes the cited credentials. |
| [GHSA-cv5c-mh6j-wvp9](https://github.com/advisories/GHSA-cv5c-mh6j-wvp9) | `09f85f2cf79362a2f7963754b49a44cb3d234176` | False-like environment values do not enable project plugin discovery. |
| [GHSA-mv8x-fg99-32mf](https://github.com/advisories/GHSA-mv8x-fg99-32mf) | `b944c6e821c2177eac6da857c99ff24566aa56b0` | Environment-file values remain opaque; embedded assignments do not create new variables. |
| [GHSA-33qv-c5qm-799v](https://github.com/advisories/GHSA-33qv-c5qm-799v) | `0dee92df22bdc0cfbcad90ca954aa14916f018de` | Memory scanning uses the shared strict threat patterns. |

Validation against identical application source on macOS: 263 targeted tests passed.
Platform-specific Linux/Windows cases were skipped by the test runner. Independent probes also
confirmed all four cited context/memory samples are blocked, seven cited or related
credential variable names are excluded, and SQLite DB/WAL/SHM are mode `0600` under
umask `022`. The workflow records the exact maintained test files and selectors;
the SQLite DB/sidecar permission probe is separate manual evidence.

## Residual fixes carried by this distribution

[GHSA-pmqc-57g8-c22c](https://github.com/advisories/GHSA-pmqc-57g8-c22c)
(CVE-2026-10224): Feishu webhook authentication now precedes the shared delivery
quota. Invalid traffic cannot consume the quota of valid callbacks behind the
same reverse proxy. Unsigned URL-verification challenges require the verification
token; ordinary signed events retain encrypt-key-only support. Body bounds and
read timeouts remain before authentication; anonymous anomaly tracking is capped
with bounded cleanup. The HTTP regression first failed on the reviewed base and
then passed for token-only, signature-only, and combined authentication. An ingress
still needs its own network-level flood protection.

[GHSA-xq8w-9jvx-gm3v](https://github.com/advisories/GHSA-xq8w-9jvx-gm3v)
(CVE-2026-10221): preserved task state is quoted assistant data after compression,
not a new user instruction or text appended to a real user request. The migration
also handles old persisted snapshots. Repeated refreshes and budget salvage retain
ordinary message content, tool-call groups, and native provider replay metadata.
Tests inspect the outgoing message roles and persisted transcript, including both
in-place compaction and session rotation. This addresses the reported promotion
of stored task text into user authority; it is not a claim that all model-level
prompt injection is impossible.

## Interpretation and future updates

These conclusions apply to the specific reported defects in reviewed repository
source. They do not establish that every possible prompt injection or sandbox
weakness is eliminated, or that an older live deployment contains these fixes.
Document each alert disposition with its source evidence; do not globally suppress
application advisories. Retain regression behavior when merging future upstream
changes, and remove a fork fix only after an equivalent upstream fix is verified.
