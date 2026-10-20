# Agent integrations

The installer uses a catalog of client profiles. Each profile describes skill locations and, where supported, an MCP configuration adapter. Finding a directory means configuration is available; it does not prove the application is installed.

## Supported global destinations

| Profile (`--agent`) | Skill directory | MCP configuration |
| --- | --- | --- |
| `codex` | `~/.agents/skills` | `$CODEX_HOME/config.toml`, default `~/.codex/config.toml` |
| `antigravity` | `~/.gemini/config/skills` | `~/.gemini/config/mcp_config.json` |
| `antigravity-ide` | Same global directory as Antigravity App | Same global file as Antigravity App |
| `claude-code` | `~/.claude/skills` | Manual setup |
| `cursor` | `~/.cursor/skills` | Manual setup |

These paths apply to local Windows and Linux installations. The catalog does not configure project files, browser clients, or remote sessions. Claude Code and Claude Desktop are different clients. Gemini CLI and Antigravity CLI are not inferred from the presence of `.gemini`.

An existing `tinta-designer` installation in `~/.codex/skills` is reused. Antigravity IDE also recognizes its older location, `~/.gemini/antigravity/skills`. Clients sharing a destination receive one installation or configuration prompt for it.

If a skill exists only in an older directory and another client needs the shared location, the installer shows both paths and asks before creating another copy. This question also applies with `--yes`; without an interactive terminal, the new copy is skipped. Existing copies are kept and updated separately. No directories are silently migrated or removed.

Skill updates replace bundled files and keep additional files. The installer prepares the new directory before replacing the installed one. If replacement fails, it restores the previous directory; if restoration also fails, it keeps that directory and prints its location for manual recovery.

## Installing and previewing

The default command installs the extension, then offers skill installation and MCP configuration for supported clients. Each MCP file requires separate approval:

```bash
uvx tinta-mcp install
```

Select a profile:

```bash
uvx tinta-mcp install --agent codex
```

Preview all steps without creating directories, backups, or files:

```bash
uvx tinta-mcp install --agent codex --dry-run
```

Repeat `--agent` to choose multiple profiles. Explicit selection permits offering a destination even when its directory does not exist yet. Without selection, the installer only offers profiles with existing configuration directories.

To run just one step:

```bash
uvx tinta-mcp install --only mcp --agent codex
uvx tinta-mcp install --only skills --agent antigravity
uvx tinta-mcp install --only extension
```

`--only mcp` does not look up Inkscape or copy extension/skill files. `--only skills` also runs without looking up Inkscape and does not configure MCP. `--only extension` handles just the Inkscape extension.

Run `tinta-mcp --help` to see the available commands, or `tinta-mcp install --help` for installation options. Running `tinta-mcp` without a command starts the MCP server.

The header identifies the package version being executed. The final summary lists the outcome of each requested operation: installed, configured, unchanged, skipped, planned (dry run), or error. A bad client path, such as a relative `CODEX_HOME`, is reported without preventing independent client operations; the command still returns a nonzero status when errors occur.

When locating the Inkscape profile, the installer tries the available executables before using a standard directory. It warns when a query fails or a fallback is needed; check a fallback path against your Inkscape preferences before installing there.

`--yes` accepts skill installation and updates, except for creating an additional copy as described above. MCP edits always require separate approval in an interactive terminal. An empty answer, EOF, or Ctrl+C skips an MCP change. Extension installation is automatic in the default flow; `--only` limits which steps run.

## Configuration changes and backups

The installer resolves `uvx` from PATH and proposes a `tinta` entry with that executable and the argument `tinta-mcp`. It does not add `@latest`. If the entry already matches, it leaves the file untouched and creates no backup. An older entry, including one pointing to TestPyPI, requires approval to update its command and arguments. Enabling a disabled entry is shown in the preview.

Other servers and compatible Tinta settings, such as environment variables and timeouts, are retained. Previews show the new managed fields, without displaying existing credentials or full configuration files. TOML comments are retained. Strict JSON is reformatted with two-space indentation. JSONC, malformed configurations, and incompatible remote entries require manual setup; the installer explains the type of conflict without printing configuration values.

Before updating an existing file, the installer creates an adjacent backup named like `config.toml.bak-<unique suffix>`, preserving the original bytes and access protections. New files need no backup. It validates the proposed content, writes a temporary file beside the destination, and replaces the destination after checking that the original still matches the approved snapshot.

To restore a backup, close the client, review the backup path printed by the installer, and copy that backup over the corresponding configuration file. Keep the backup until you have confirmed the restored client works.

The snapshot check detects changes made since the preview, but it is not a universal lock against other applications. Close configuration editors while applying a change. Each file is handled independently; a failure does not undo another client's successful setup. Errors are reported and return a nonzero exit status; choices to skip a step do not.

Resolved destinations are shown. Automatically detected symlinks and Windows junctions require explicit profile selection; a linked skill directory itself requires manual installation. A new redirection appearing at the time of an MCP write is rejected.

Restart Codex after updating its configuration. For Antigravity, reload MCP servers or restart the client. Restart Inkscape after updating the extension.

## Adding a client

1. Confirm the client's global skill location, MCP format, and any environment overrides in its official documentation. Record the sources and supported scope here.
2. Add an `AgentProfile` in `tintamcp/agents/catalog.py`. Give it a stable CLI identifier, a display name, and its `PathRule` entries. Use `mcp=None` when automatic MCP configuration is unsupported.
3. Reuse `json-mcpServers` for strict JSON with a top-level `mcpServers` object, or `toml-codex` for Codex's TOML layout. Sharing a file does not make two formats compatible: choose an adapter only after checking the schema.
4. Add a new adapter in `configuration.py` only when the schema requires different behavior. The coordinator and writer should not need client-specific branches.
5. Add temporary-directory tests covering detection, explicit selection, existing installations, shared destinations, and a representative synthetic configuration. Never commit real credentials or personal configuration files as fixtures.
6. Run the quality gates and installed-wheel smoke test described in the repository's development and release guidance.

## Profile references

Paths verified on October 20, 2026:

- [Codex local skills](https://learn.chatgpt.com/docs/build-skills) and [MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
- [Antigravity skills](https://antigravity.google/docs/skills) and [MCP configuration](https://antigravity.google/docs/mcp).
- [Claude Code skills](https://code.claude.com/docs/en/skills).
- [Cursor skill directories](https://cursor.com/docs/skills).
