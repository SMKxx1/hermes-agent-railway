# Railway deployment

This guide covers the published Railway template and the manual fork workflow.
Use a repository and Railway account that you control; this guide contains no
project IDs, deploy hooks, or credentials.

## One-click template

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/template/hermes-agent-with-authenticator-2fa)

The public template is configured to create one service, attach a persistent
volume at `/opt/data`, select `Dockerfile.railway` through the shared
`RAILWAY_DOCKERFILE_PATH` variable, and generate an HTTPS domain targeting
port `9119`. Its three owner inputs remain blank until deployment:
`HERMES_DASHBOARD_TOTP_AUTH_USERNAME`,
`HERMES_DASHBOARD_TOTP_AUTH_PASSWORD`, and `OPENROUTER_API_KEY`.

1. Click **Deploy on Railway** and select your workspace.
2. Enter your own dashboard username, a password of at least 12 characters,
   and `OPENROUTER_API_KEY`.
3. Deploy and open the generated HTTPS URL. The first successful login starts
   TOTP enrollment; scan the QR code and save the recovery codes.

A fresh template instance passed login, authenticator enrollment, a real
OpenRouter response, and persistence across container replacement. See the
[validation record](CHANGES.md) for scope. The manual fork workflow below
remains available when you need to inspect or customize every setting.

## Manual fork deployment

### Service settings

Create one Railway service from the repository and set its Dockerfile path to
`Dockerfile.railway`. Run **one replica**. Attach one persistent volume at
`/opt/data`; this volume is required for the generated signing material, TOTP
factor state, SQLite databases, sessions, memories, and workspace files. A
second replica would not share those SQLite and authentication semantics.

Configure the service's public HTTP port as `9119` and use `GET /api/status` as
the health check. The endpoint is intentionally available to the platform
probe; the rest of the dashboard requires authentication. The bootstrap
derives the public URL from Railway's `RAILWAY_PUBLIC_DOMAIN` when available.
For a custom domain, override it explicitly:

```text
HERMES_DASHBOARD_PUBLIC_URL=https://<your-custom-domain>
```

Keep the service on one replica during upgrades. Deploy a new image or source
revision while retaining `/opt/data`; the image does not auto-update itself.

## Updating an existing service

A GitHub push updates Railway only when automatic deployments are enabled for
that service and the push reaches its connected branch. A pull-request branch
does not update a service connected to `main`. Template deployments can have
automatic deployments disabled even though Source shows a repository and branch;
check the service's deployment settings instead of assuming the connection is
enough. See [Railway's autodeploy guide](https://docs.railway.com/deployments/github-autodeploys).

Review and merge the fix or upstream-sync pull request after Railway CI passes.
With autodeploy enabled, that merge triggers a build; enable **Wait for CI** if
deployments should also wait for checks on the merged commit. With autodeploy
disabled, deploy the latest source commit explicitly after merging. Confirm the
deployment's commit and successful health check before calling the upgrade done.

Keep the existing `/opt/data` volume and authentication variables attached. A
normal upgrade replaces the container, not the persistent workspace. Commit
builds expose a `git.<revision>` version so the dashboard can be matched to the
deployed source without inventing a release number.

## Optional Railway IaC recipe

`.railway/railway.ts` describes the same service and volume for a **new**
Railway project. It has not been applied to Railway from this repository. With
Node.js 22 or newer and Railway CLI 5.42.1 or newer, install its isolated
dependency set:

```bash
npm ci --prefix .railway
```

Before planning, create the three shared variables named in the recipe in the
new project. `ctx.shared` references existing values; it does not create or
populate secrets. Connect the source repository to the service named `hermes`,
then inspect the generated plan before applying it:

```bash
railway config plan
railway config apply
```

Apply only to a new project you control. Generate the public domain separately
for port `9119`; if you change regions, move both the service and its volume
before the first apply. This recipe performs no cloud setup by itself and CI
does not verify a Railway project.

## Variables

Set these before the first boot. TOTP is the default; the OIDC configuration
below replaces its username/password requirements.

| Variable | Required value |
| --- | --- |
| `OPENROUTER_API_KEY` | Your OpenRouter key. |
| `HERMES_DASHBOARD_TOTP_AUTH_USERNAME` | The dashboard owner name, 1–128 characters. |
| `HERMES_DASHBOARD_TOTP_AUTH_PASSWORD` | The initial dashboard password, 12–1024 characters. |
| `HERMES_DASHBOARD_PUBLIC_URL` | Optional HTTPS dashboard origin override; otherwise Railway's assigned domain is inferred. |

The image enables the public dashboard on port `9119` by default. Set these
explicitly only when overriding the image defaults:

```text
HERMES_DASHBOARD=1
HERMES_DASHBOARD_HOST=0.0.0.0
HERMES_DASHBOARD_PORT=9119
```

You can use a precomputed scrypt password hash instead of the plaintext
bootstrap password by setting `HERMES_DASHBOARD_TOTP_AUTH_PASSWORD_HASH` and
omitting `HERMES_DASHBOARD_TOTP_AUTH_PASSWORD`. The hash must begin with
`scrypt$`. Generate one locally with
`python -m plugins.dashboard_auth.totp hash-password`; never put either form in
a committed file.

The bootstrap creates a random `HERMES_DASHBOARD_TOTP_AUTH_SECRET` and
`API_SERVER_KEY` on the private volume when they are absent. These are stable
per-instance values and are not printed. You may provide either value yourself
as a Railway variable; supplied values must contain at least 32 characters.

Add provider and channel variables only when you use those integrations. Common
examples include `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`,
`TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`, and `SLACK_BOT_TOKEN`. Railway
variables are authoritative: rotating or removing one does not revive a stale
copy from the volume.

## OIDC login and owner policy

To use an external identity provider, supply these Railway variables:

| Variable | Value |
| --- | --- |
| `HERMES_DASHBOARD_OIDC_ISSUER` | The provider's HTTPS issuer URL; Google uses `https://accounts.google.com`. |
| `HERMES_DASHBOARD_OIDC_CLIENT_ID` | The OAuth application's client ID. |
| `HERMES_DASHBOARD_OIDC_CLIENT_SECRET` | The client secret for confidential clients such as Google web applications; omit for a public client. |
| `HERMES_DASHBOARD_OIDC_ALLOWED_SUBJECTS` | JSON list of permitted immutable OIDC `sub` values, for example `["owner-subject-id"]`. |
| `HERMES_DASHBOARD_OIDC_ALLOWED_EMAILS` | Google only: JSON list of owner Gmail or Google Workspace addresses, for example `["owner@example.com"]`. The ID token must contain `email_verified: true`, and a Workspace address also requires Google's `hd` claim. |
| `HERMES_DASHBOARD_OIDC_SCOPES` | Optional space-separated scopes, default `openid profile email`; must include `openid`, and `email` when using the Google email allowlist. |

At least one owner list must be nonempty. A login is authorized when either
its subject or its verified Google email matches. Email matching ignores case
but does not collapse dots, aliases, or domains. A configured issuer and client
ID alone do not grant dashboard access. Use literal identities without surrounding
spaces in both JSON lists. Partial OIDC settings stop bootstrap
with an error before it initializes the volume.

Google accounts using a third-party email address without a Workspace `hd`
claim must use the subject allowlist. Google does not remain authoritative for
ownership of those external mailboxes; `email_verified` alone is insufficient.
See [Google's ID-token verification guidance](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token).

Register `https://<dashboard-domain>/auth/callback` as an authorized redirect
URI in your identity provider's web application. If the dashboard uses a custom
domain, set `HERMES_DASHBOARD_PUBLIC_URL` to that HTTPS origin and register the
matching callback. Supply client credentials in Railway; do not commit the
downloaded OAuth JSON or copy it into the image or persistent volume. Enable
the identity provider's MFA policy for the owner account.

The bootstrap selects `dashboard.auth_providers: [self-hosted]` and writes the
owner lists to `dashboard.oauth.self_hosted.allowed_subjects` and
`allowed_emails` in the managed configuration. Their managed environment
copies also remain authoritative over stored configuration. Owner authorization
is checked on login, session verification, bearer authentication, and refresh.
Removing an owner and redeploying therefore denies that owner's existing
sessions as well as new logins.

During OIDC mode, managed blank TOTP credentials prevent a stale password from
activating the exclusive TOTP provider. The signing secret and
`dashboard-totp-auth.sqlite3` remain on the volume unchanged. To return to TOTP,
remove or blank **all six** OIDC variables above, restore the TOTP username and
password (or password hash), and redeploy with the same volume and signing
secret. The existing authenticator and recovery state remain usable; stale
OIDC settings from the volume cannot reactivate OIDC. Do not change the signing
secret as part of a login-mode switch.

## TOTP first login and recovery

With no OIDC settings supplied, the Railway bootstrap writes
`dashboard.auth_providers: [totp]`. This provider is exclusive, so OAuth and the
legacy single-factor dashboard providers are not used in TOTP mode.

1. Open the assigned HTTPS dashboard and submit the configured username and password.
2. On the first successful password check, scan the displayed `otpauth` QR code (or open its authenticator link).
3. Enter the six-digit code from the authenticator app.
4. Store all displayed recovery codes in an offline password manager. Each code is one-use.

Subsequent logins require both the password and an authenticator code. A
recovery code invalidates existing sessions and the old authenticator, then
starts a five-minute enrollment in that browser. Password-only logins cannot
replace this enrollment. If it expires or the browser is closed, sign in again
and use another unused recovery code. Completing enrollment rotates all
remaining recovery codes. There is no HTTP bypass for the factor. If both the
authenticator and recovery codes are unavailable, run
this owner-only command over Railway SSH from `/opt/hermes`:

```bash
/command/s6-setuidgid hermes /opt/hermes/.venv/bin/python -m plugins.dashboard_auth.totp reset --confirm
```

It invalidates the factor, recovery codes, and all sessions; the next login
must enroll a new authenticator. Keep the signing secret unchanged. If it is
lost, restore it from backup before attempting recovery. Do not delete the
volume or the TOTP SQLite database by hand.

When upgrading an existing instance, an enrollment already in progress remains
usable until its original expiry. Older versions immediately deleted all
recovery codes when recovery began, so an expired pre-upgrade recovery requires
the owner-only reset above. An older pending first enrollment after a password
change or local reset has the same stored state as recovery; the migration
conservatively protects that enrollment too. Complete it before expiry or use
the owner-only reset to start again. Password-only access never reopens an
ambiguous enrollment automatically.

## Configuration and models

`deploy/railway/defaults.yaml` contains shared first-boot settings. It seeds
OpenRouter with `openai/gpt-5.4-mini`, a local terminal backend rooted at
`/opt/data/workspace`, unattended code execution (`approvals.mode: "off"`),
and loopback-only API-server wiring. Existing settings are preserved on restart;
the execution mode is filled in only if it is absent. Change the provider and model
from the dashboard, then keep the corresponding provider key in Railway.

The source tree remains a full Hermes fork. The custom model-orchestration and
research code is removed from this public deployment, while the native Hermes
agent framework and its built-in `delegate_task` capability remain available.

## MCP servers and custom code

The [bundled catalog](../../optional-mcps/README.md) combines the upstream Hermes
catalog with a few additional presets for files, git, search and web fetching.
Browse them in the dashboard's **MCP** page or with `hermes mcp catalog`.

Agents can install servers directly through their terminal:

```bash
hermes mcp install time
hermes mcp install github
hermes mcp add my-server --command python --args /opt/data/workspace/server.py
hermes mcp test my-server
```

Supply required API keys in Railway service variables or the catalog form;
Railway variables take precedence. For example, GitHub's preset uses
`MCP_GITHUB_API_KEY`, while Brave Search uses `BRAVE_API_KEY`. Credentials are
forwarded only to the server that declares them, and `config.yaml` keeps a
`${NAME}` reference rather than the value.

Agent commands have no terminal, so the installer never waits for input: a
missing required credential fails with an error that names it. OAuth
installation saves the connection; finish authorization using **Authenticate**
in the dashboard's MCP page. This uses the public dashboard callback instead of
the container's localhost. Start a new conversation after setup to load the
tools.

Custom Python, JavaScript, and shell code runs inside the Railway container as
the `hermes` user. Put projects and virtual environments in `/opt/data/workspace`
so they survive deployments. Python project dependencies can be installed with:

```bash
cd /opt/data/workspace/my-project
uv venv .venv
uv pip install --python .venv/bin/python PACKAGE
.venv/bin/python script.py
```

Use `npm install` inside a JavaScript project or `npm install -g PACKAGE` for a
CLI. Global npm binaries use `/opt/data/.local/bin`, already on `PATH`, and npm/uv
caches persist on the volume. The workspace gets an initial `AGENTS.md` with this
runtime and MCP guidance; an existing workspace instruction file is preserved.

If an older volume already has an explicit `approvals.mode: smart` or `manual`,
switch it once in the dashboard configuration or run this over Railway SSH:

```bash
hermes config set approvals.mode off
```

The bootstrap preserves explicit owner choices across subsequent restarts.

## Build locally

The build pins the upstream image and its source revision together, then rebuilds
the Python environment, dashboard, TUI, and Photon sidecar from this checkout's
lockfiles. This allows reviewed dependency security fixes to reach the deployed
runtime before upstream publishes them. Old dependency trees and compiled assets
are replaced, so updating a lockfile cannot leave the inherited version in use.

The image records `RAILWAY_GIT_COMMIT_SHA` when Railway supplies it. For a
local build, the revision argument is optional:

```bash
docker build \
  -f Dockerfile.railway \
  -t hermes-agent-railway:local .
```

Do not pass real credentials as build arguments. Provide them only at runtime
through Railway variables. No Railway cloud deployment is performed or
verified by this repository's CI.
