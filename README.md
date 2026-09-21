# Hermes Agent on Railway

A reusable, single-owner Railway deployment of [Hermes Agent](https://github.com/NousResearch/hermes-agent), with a web dashboard protected by a username, password, and authenticator-app two-factor authentication.

This community distribution retains the Hermes agent, tools, messaging integrations, memories, and native agent delegation. The custom model-orchestration and route-research feature has been removed. It is not an official Nous Research or Railway release.

## Deploy with the public Railway template

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/template/hermes-agent-with-authenticator-2fa)

The published template deploys this public source tree with one service, a
persistent volume at `/opt/data`, and an HTTPS domain targeting port `9119`.

1. Click **Deploy on Railway** and choose your Railway workspace.
2. Provide your own dashboard username, a password of at least 12 characters,
   and an `OPENROUTER_API_KEY`.
3. Deploy, then open the HTTPS dashboard and enroll your authenticator app on
   the first login. Save the recovery codes.

Railway hosting and model usage are billed to your own accounts. The template
configuration has been audited; the source deployment validation is recorded in
the [release notes](deploy/railway/CHANGES.md).

## Deploy manually from a fork

1. Fork this repository and create a **new Railway service** from your fork. The default `Dockerfile` points to `Dockerfile.railway`.
2. Before deploying, attach a persistent volume at **`/opt/data`** and set these service variables:

   | Variable | Your value |
   | --- | --- |
   | `OPENROUTER_API_KEY` | Your model-provider API key. |
   | `HERMES_DASHBOARD_TOTP_AUTH_USERNAME` | Your dashboard username. |
   | `HERMES_DASHBOARD_TOTP_AUTH_PASSWORD` | A unique password of at least 12 characters. |

3. Keep **one replica**, disable sleeping, set the health check to **`/api/status`**, and generate an HTTPS domain targeting port **`9119`**. Leave the start command at the image default, or use `/opt/hermes/docker/entrypoint-dispatch.sh gateway run`.
4. Open the dashboard, sign in, scan the setup QR with Google Authenticator or another TOTP app, verify a code, and save the recovery codes.

The initial model is `openai/gpt-5.4-mini` through OpenRouter. You can choose another provider/model in the dashboard and supply your own corresponding key in Railway. Railway hosting and model usage are billed to your own accounts.

See the [deployment guide](deploy/railway/README.md) for password hashes, custom domains, messaging, backup/recovery, local builds, and the optional [infrastructure recipe](.railway/railway.ts).

## What is shared and what is yours?

| Shared in this repository | Entered by each owner | Generated privately on `/opt/data` |
| --- | --- | --- |
| Application code and dashboard | Dashboard username and password/hash | Stable signing/encryption secret |
| Pinned container base and startup logic | Model-provider API keys | TOTP enrollment and recovery-code hashes |
| Default model and deployment wiring | Messaging tokens and allowed users | Internal gateway key |
| Auth policy and deployment recipe | Optional custom domain and preferences | Sessions, memories, workspace, channel state |

Credentials are runtime variables, never Docker build arguments. Railway variables override saved credential copies, including when a managed variable is removed. Back up the private volume; its authentication state is required across restarts and upgrades. Never copy one owner's volume into another owner's deployment.

## Development and maintenance

```sh
docker build -t hermes-agent-railway:local .
```

The Docker build validates that dependency manifests match the digest-pinned base before overlaying this source. If dependencies change, build and pin a compatible base using `Dockerfile.upstream`. Backend-only edits reuse the dashboard build cache.

Python checks run through `scripts/run_tests.sh`; CI also builds the image and verifies fresh enrollment, protected endpoints, restart persistence, file permissions, and startup failures. See [release notes](deploy/railway/CHANGES.md) for the changes and known limits.

## Attribution

Hermes Agent is built by [Nous Research](https://nousresearch.com) and distributed under the [MIT license](LICENSE), retained here. The [upstream README](README.upstream.md) and [official documentation](https://hermes-agent.nousresearch.com/docs/) describe the underlying agent. This repository's Railway guide describes the deployment-specific behavior.
