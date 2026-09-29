# Bundled MCP catalog

This Railway distribution ships the upstream Hermes catalog plus a few additional
server presets. Entries are available in the dashboard and `hermes mcp catalog`;
they are installed only when selected. The additional presets are maintained by
this distribution and do not imply Nous Research endorsement.

| Area | Additional presets in this distribution |
| --- | --- |
| Files and development | filesystem, git, github, memory, sequential-thinking |
| Web and documentation | fetch, brave-search, firecrawl, cloudflare-docs |
| Utilities | time |

Everything else in `optional-mcps/` comes from upstream. Each manifest links to
the server's own documentation. Local launchers are pinned to exact npm/PyPI
releases; the additional pins were checked against the package registries on
2026-09-24 and were published before 2026-09-10. Remote endpoints are operated
and updated by their respective providers.

## Agent-driven setup

```sh
hermes mcp catalog
hermes mcp install time
hermes mcp add custom --command python --args /opt/data/workspace/server.py
hermes mcp test custom
```

Commands run by the agent have no terminal, so they never wait for input. Supply
credentials as Railway variables (for example `BRAVE_API_KEY`) or through the
dashboard credential form before installing; a missing required credential makes
the install fail with an error that names it. Local (stdio) presets receive only
the credentials they declare: `config.yaml` stores a `${NAME}` reference, never
the value.

For OAuth servers, installation saves the connection; use **Authenticate** on
the dashboard MCP page to sign in through your browser. Installation alone does
not authorize the remote account. Start a new session to load the configured
tools. See [the Railway guide](../deploy/railway/README.md) for custom runtimes
and persistent storage.
