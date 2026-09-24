# Railway agent workspace

This is the persistent workspace for a single-owner Hermes deployment on Railway.
You can write and run custom Python, JavaScript, shell scripts, and MCP servers
here using `terminal` and `execute_code`. The local backend executes inside the
Railway container. Resolve the active profile's paths from `$HERMES_HOME`.

## Dependencies and custom code

- Keep projects, scripts, and virtual environments under `$HERMES_HOME/workspace`.
- For Python dependencies, create a project venv with `uv venv .venv`, install
  with `uv pip install --python .venv/bin/python PACKAGE`, and run scripts with
  `.venv/bin/python`. The Hermes application venv under `/opt/hermes` is read-only.
- Use `npm install` in a project directory for JavaScript dependencies. Global
  npm CLIs use the writable `/opt/data/.local` prefix, whose bin directory is
  on PATH. `npx -y PACKAGE@VERSION` and `uvx PACKAGE==VERSION` can launch MCPs.
- Files under `/opt/data` survive redeploys. Use exact package versions for
  repeatable installs. Use `terminal` for installing dependencies and CLIs.

## Installing MCP servers for the user

1. Inspect the available presets with `hermes mcp catalog`.
2. Install a preset with `hermes mcp install NAME --yes`. For a custom server,
   use `hermes mcp add NAME --yes --command COMMAND --args ARGUMENTS`.
   Put all Hermes options before `--args`, which consumes the rest of the command.
3. For a remote endpoint, use `hermes mcp add NAME --yes --url URL --auth none`,
   `--auth header`, or `--auth oauth`, as required by that server.
4. Run `hermes mcp test NAME` and check its exit status. A saved connection is
   not proof that authentication or a tool call works.
5. OAuth servers need the owner to click **Authenticate** on the dashboard MCP
   page. A browser callback to localhost inside Railway is not the owner's browser.
6. Required API keys can come from Railway variables or the dashboard form.
   Report missing key names to the owner without requesting secrets in chat.
   Non-secret catalog settings can be supplied with `--env KEY=VALUE`.
7. Start a new conversation to load new tools. Preserve the current conversation's
   toolset and system prompt; do not restart the gateway during an active task.

`--no-probe` saves a server configuration before the service is reachable. Report
that it still needs testing. MCPs need only be installed when they help the task.
