# Bundled MCP catalog

This Railway distribution combines the upstream Hermes catalog with additional
server presets. Entries are available in the dashboard and `hermes mcp catalog`;
they are installed only when selected. Additional presets are maintained by this
distribution and do not imply Nous Research endorsement.

| Area | Presets |
| --- | --- |
| Files and development | filesystem, git, github, memory, sequential-thinking |
| Web and documentation | fetch, brave-search, firecrawl, context7, deepwiki, microsoft-learn, aws-knowledge, cloudflare-docs |
| Workspaces and projects | notion, linear, atlassian, figma |
| Data and operations | supabase, neon, sentry, stripe, n8n |
| Utilities and creative work | time, comfy-cloud, unreal-engine |

Each manifest links to the server's own documentation. Local launchers are pinned
to exact npm/PyPI releases; the additional pins were checked against the package
registries on 2026-09-24 and were published before 2026-09-10. Remote endpoints
are operated and updated by their respective providers.

## Agent-driven setup

```sh
hermes mcp catalog
hermes mcp install time --yes
hermes mcp add custom --yes --command python --args /opt/data/workspace/server.py
hermes mcp test custom
```

`--yes` skips interactive prompts. Closed-stdin agent commands use the same
non-interactive behavior automatically. Supply credentials in Railway variables
or the dashboard credential form. Missing required credentials produce an error
that names the missing setting. For non-secret install settings, use for example
`hermes mcp install n8n --yes --env N8N_BASE_URL=https://n8n.example.com`.

For OAuth servers, installation saves the connection; use **Authenticate** on
the dashboard MCP page to sign in through your browser. Installation alone does
not authorize the remote account. Start a new session to load the configured
tools. See [the Railway guide](../deploy/railway/README.md) for custom runtimes
and persistent storage.
