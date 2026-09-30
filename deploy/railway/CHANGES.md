# Railway distribution changes

## Dashboard reliability and packaged diagnostics (2026-09-30)

- Model switching starts a fresh terminal instead of reattaching the previous
  session on reload. The sidebar distinguishes the configured default from the
  model reported by the terminal, and its connection badge follows the terminal.
- Side-panel reconnects cancel obsolete timers and do not replay events from
  disposable sessions that the server has already retired.
- Late socket events and deferred callbacks from the previous terminal cannot
  clear the replacement connection or send old commands into the new chat.
- Diagnostic subprocesses use the sealed image's Python environment. Railway
  images regenerate their own version/provenance stamp, and dashboard status
  surfaces display the canonical version, including commit-based builds.
- The terminal welcome banner now receives that same display version through
  session metadata. An image contract renders the compiled TUI in a real terminal
  to prevent commit builds from showing `vunknown`.
- Skill enable/disable switches expose their skill names to assistive technology.
- Railway CI includes dashboard regressions, type checks, a production web build,
  and image contracts for diagnostic commands and version identity. The deployment
  guide explains that a GitHub connection alone does not enable automatic deploys.
- The security follow-up updates PyJWT to 2.14.0 and OAuthlib to 4.0.0. OAuthlib's
  exact security pin has a quarantine exception that can be removed after
  2026-10-12; the remaining quarantine and full advisory checks stay enabled.
- npm security fixes update `brace-expansion` to 5.0.12 in the root and website
  locks, `ip-address` to 10.7.1, and desktop Electron to 41.10.7. The vendored
  Nix node-gyp lock uses fixed `brace-expansion` 1.1.21, 2.1.7, and 5.0.12,
  with its dependency hash refreshed and the derivation rebuilt successfully.

## Application security regressions (2026-09-29)

- Feishu webhook requests authenticate before consuming the shared delivery
  quota. Anonymous error tracking is bounded, malformed envelopes fail closed,
  and URL verification requires a configured verification token.
- Compression preserves task state as quoted assistant data rather than user
  instructions. Existing task snapshots migrate without promoting their content,
  while retained messages and provider replay metadata stay usable.
- Railway CI retains source regressions for these defects and eight application
  advisories whose fixes are already present. The editable application version
  remains unchanged; see [the source review](application-security-reviews.md).

## Catch-up validation and dependency repairs (2026-09-29)

- Corrected the Docker custom-MCP contract for MCP 2's `MCPServer` API,
  workspace-owned dependencies, and the CLI's tool-enablement confirmation.
  Local image builds explicitly select `Dockerfile.railway`.
- The workflow guard now sweeps sync branches and trusted pull-request activity,
  runs after upstream sync, and cancels late runs even for already-disabled
  workflows. It never checks out pull-request code. Disabling remains reactive:
  GitHub can start a newly registered workflow before the guard cancels it.
- Retained the strict dependency security gate and carried temporary fixes in
  the fork's Python and npm locks. The image now rebuilds Python dependencies,
  dashboard/TUI bundles, and Photon dependencies from those locks, replacing
  inherited artifacts. The pinned upstream image still supplies its toolchain
  and service layout.
- Python auditing covers both application and package-manager locks. The three
  direct-source dependencies have exact-artifact provenance reviews and ongoing
  advisory checks; changed sources or unreviewed advisories fail the gate.
- Desktop development dependencies include Electron 41.10.3 to cover both
  reported Electron vulnerabilities. Its disabled release workflow accepts the
  lockfile's updated dependency location. The desktop app is not part of the
  Railway image.

## Fork of NousResearch/hermes-agent with weekly upstream sync (2026-09-29)

- This repository is now a GitHub fork of NousResearch/hermes-agent with the
  distribution's history rebuilt on the upstream commit it was cut from, and
  caught up to upstream `536802c`. A weekly workflow opens a pull request with
  new upstream commits; nothing reaches `main` until it is merged by hand.
- Fork-specific entrypoints reduce merge conflicts: the distribution
  README moved to `.github/README.md`, its workflows are `railway-*.yml`, the
  root `Dockerfile` is upstream's, and `railway.toml` selects
  `Dockerfile.railway`. Upstream workflows are kept but disabled here; the
  dependency repairs above are reviewed temporary deviations.
- `Dockerfile.railway` layers on the published upstream image built from the
  merged commit (Python 3.14 and the pinned PM toolchain); the sync moves the
  commit and the image digest together. `uv`/`uvx` stay on the agent's `PATH`.
- Upstream now provides the MCP catalog (67 entries), non-interactive installs,
  and an OpenAI-compatible STT path. The distribution's own MCP installer and
  Qwen transcription code were dropped in favour of upstream's; Qwen ASR on
  OpenRouter is plain configuration (`stt.openai.base_url`/`api_key`), and the
  additional stdio presets now forward only their declared credentials.
- Re-applied on top of upstream: TOTP login, the provider allowlist, same-origin
  checks, the account-wide login rate limit, cross-process config locking,
  literal managed credentials, symlink-safe uploads and file writes, and
  Telegram/WhatsApp audio transcription.

## MCP catalog and agent execution

- Expanded the catalog from 5 to 25 presets with documented provider endpoints
  and exact-version local packages.
- Fixed agent-driven MCP setup cancelling at an interactive prompt, failed
  connections returning success, and catalog credentials never reaching stdio
  servers. Added `--yes`, `--no-probe`, declared catalog settings, and headless
  OAuth configuration followed by dashboard authentication.
- Dashboard installs now validate inputs without prompting or blocking on a
  server connection. Existing Railway credentials can be used with blank fields.
- Railway defaults allow unattended custom code via `approvals.mode: "off"`.
  Missing execution policy is backfilled on older volumes; explicit owner
  settings are preserved. User npm installs and package caches use the volume.
- Added persistent workspace instructions, real subprocess MCP regression tests,
  and an image contract for non-root Python, npm, and custom MCP execution.

## Deployment and configuration

- Added a default Docker entry point for source imports, a digest-pinned thin image, first-boot defaults, and an optional Railway TypeScript infrastructure recipe.
- Added mandatory username/password + RFC 6238 TOTP dashboard authentication. Enrollment uses a locally generated QR code, an encrypted private factor store, one-time recovery codes, bounded challenges, replay protection, and owner-only shell recovery.
- Railway credentials are held in a protected managed overlay. Changing or removing a variable cannot revive its old copy in the volume's `.env`. Instance-generated secrets remain stable on the persistent volume.
- Removed the custom model orchestrator, its virtual provider and runtime routing, route research, dashboard editor, and API endpoints. Native Hermes tool delegation remains available.
- Removed private deployment notes, contributor contact mappings, unrelated benchmark artifacts, and inherited publishing workflows. Retained the upstream MIT license. CI checks source and Docker behavior without provisioning Railway or publishing artifacts.

## Bugs fixed

- A fresh image could not seed `.env` because both the derivative and pinned base excluded `.env.example`; the internal API key was consequently never generated. Bootstrap now creates a safe instance file explicitly.
- Dashboard availability depended on undocumented environment settings. The image now supplies port and dashboard defaults.
- The one-click template could let Railpack select an unsupported Python wheel build, and its root `Dockerfile` symlink was not detected. The template now supplies the shared `RAILWAY_DOCKERFILE_PATH=Dockerfile.railway` build selector and port `9119`.
- Stored dotenv credentials overrode Railway rotations. The managed overlay now makes Railway-owned variables authoritative, including removals.
- Managed credential parsing interpreted literal dollar expressions differently between the runtime and settings layer. Both now share one dotenv parser with interpolation disabled.
- Docker config migration failures were logged and ignored. Startup now fails before application services when migration fails.
- Credential files created by a root shell could become unreadable to the supervised user. Startup repairs ownership and private permissions for the supported credential files and TOTP database.
- Railway SSH does not include the s6 command directory in `PATH`. Recovery documentation now invokes `/command/s6-setuidgid` explicitly.
- Telegram/WhatsApp audio handling now passes speech audio to transcription while retaining non-speech audio as attachments. Qwen transcription and its no-fallback behavior are preserved.

## Performance and maintenance

- Dashboard compilation is cached independently of backend-only changes.
- Application dependencies are installed from this repository's locks over the pinned OS base. Both dashboard and TUI bundles are rebuilt; inconsistent manifests fail the locked install.
- Managed dotenv loading reuses the existing parsed-file cache, avoiding repeated parsing and writes.
- Password hashing and authentication database operations run outside the async request event loop. SQLite connections close explicitly, and concurrent first-time factor initialization is serialized.

## Supported scope and known limits

This is a single-owner dashboard deployment with one replica and one persistent volume. It is not a multi-user hosted service. Sessions, memories, workspace data, factor state, and generated secrets belong to the individual deployment. Authenticator codes use the standard six-digit, 30-second format with a small clock tolerance.

Keep the signing/encryption secret stable and back it up with the private volume. Changing that secret makes previously encrypted TOTP state unreadable and fails authentication closed; restore the matching secret instead of deleting state blindly. Changing the username/password revokes existing sessions. Logout revokes all sessions for the one configured owner.

Local tests use disposable Docker volumes and synthetic credentials. Cloud
checks below used separate test services and a temporary provider key. Each
owner should verify their own deployment and chosen integrations.
The public template `hermes-agent-with-authenticator-2fa` is published with
the dashboard username, dashboard password, and `OPENROUTER_API_KEY` as
deployment inputs. Shared defaults select `Dockerfile.railway` and port `9119`;
no owner credentials are included. This source tree does not publish a prebuilt
derivative image.

## Cloud validation record — 2026-09-21

- Public commit `3f8374b` passed a fresh Railway source deployment check for the
  image build, HTTPS dashboard, gateway startup, complete TOTP enrollment,
  protected APIs, and persistence of the authentication session, factor, and
  workspace across an actual replacement deployment.
- A temporary marker outside `/opt/data` disappeared during replacement while
  the persistent volume state remained. An initial Railway volume attachment
  issue was fixed by reattaching the volume with the CLI in a new test project;
  no application code changes were required.
- A fresh instance created from the corrected published template built and
  started without manual configuration changes. HTTPS, unauthenticated API
  rejection, password-to-TOTP enforcement, and fresh authenticator enrollment
  passed. The `/opt/data` mount was confirmed inside the container.
- Replacing that template-created container retained the authenticated session,
  enrolled factor, and workspace marker; its `/tmp` marker disappeared.
- Both the source service and the template instance completed a real Hermes
  one-shot request through OpenRouter using `openai/gpt-5.4-mini`. The assistant
  response was read back through the authenticated dashboard session API.
  Combined usage estimates for the two smoke tests were approximately $0.026.
- The temporary provider key was removed from all three test services. After
  replacement, checks found no usable copy in process environments, managed or
  persistent dotenv files, or the credential-pool store.
- Real messaging integrations and interactive browser chat input were not
  exercised by these model tests.

A broader state regression run identified an existing FTS query-shape assertion failure in `tests/test_hermes_state.py`; the affected `hermes_state.py` is identical to the pinned source baseline. That unrelated test was not changed or disabled.
