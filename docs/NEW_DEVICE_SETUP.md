# IBWD: full setup on another device

Install IBWD once on the device, then run one setup command for each repository. The default setup supports **both Codex and Claude Code**.

These commands use a macOS/Linux terminal with Bash or Zsh. On Windows, use a Linux environment such as WSL and install IBWD and both coding clients inside that same environment. Native PowerShell hook generation is not implemented. This guide has been checked against the current IBWD code; the new device itself has not been tested.

IBWD installation downloads Python dependencies. Its setup, indexing, checks and reporting run locally without model requests. Normal coding conversations remain subject to your coding client's account and usage arrangement. No Ollama installation, model downloads, extra API keys or paid benchmark runs are needed.

## 1. Check prerequisites

You need Git, Python **3.11 or newer** with `venv`/pip support, and access to the repositories you want to work on.

```bash
git --version
python3 --version
python3 -m venv --help
```

If `python3` is older than 3.11, install a supported Python first. If your supported interpreter is named `python3.12`, use that name in the environment-creation command below. On Linux, a missing `venv` module may require your distribution's matching Python venv package.

Keep the IBWD installation at a stable location: client settings will contain its absolute interpreter path.

## 2. Get the current IBWD source

**Choose A or B, not both.**

Use **Option B (GitHub clone)** for the published version that includes this guide. Option A is an alternative for transferring a source snapshot directly, including any newer unpublished work. Whichever option you choose, the feature checks in section 3 confirm that the source includes unified setup and automatic reporting.

### A. Transfer the current source from the original device

On the original device, create a source-only archive. The source path below is the checkout used for this implementation:

```bash
mkdir -p "$HOME/Downloads"
tar --exclude='__pycache__' --exclude='*.pyc' \
  -czf "$HOME/Downloads/ibwd-source.tar.gz" \
  -C /Users/ishraklatif/Documents/claude_codex_skill/ibwd \
  pyproject.toml src docs README.md DEVELOPMENT.md KNOWN_LIMITATIONS.md
```

Transfer `ibwd-source.tar.gz` to the new device's Downloads folder using your usual file-transfer method.

On the new device, extract into a **new, empty installation directory**:

```bash
mkdir -p "$HOME/Tools"
mkdir "$HOME/Tools/ibwdv1"
tar -xzf "$HOME/Downloads/ibwd-source.tar.gz" -C "$HOME/Tools/ibwdv1"
cd "$HOME/Tools/ibwdv1"
```

If `mkdir` reports that `ibwdv1` already exists, stop and choose a new installation directory or inspect the existing installation. Do not blindly extract over it.

The archive includes the Python package and documentation, including uncommitted source changes. It does not include `.git`, `.venv`, project indexes, client settings, credentials or session logs. It is a source snapshot, not a Git checkout, and does not include the development test suite.

### B. Clone from GitHub

Use this when the remote revision contains the new setup/reporting implementation:

```bash
mkdir -p "$HOME/Tools"
git clone https://github.com/ishraklatif/ibwdv1.git "$HOME/Tools/ibwdv1"
cd "$HOME/Tools/ibwdv1"
```

If access is restricted, authenticate to GitHub using your normal method. Do not embed credentials in these commands. If the destination already exists, use the existing checkout after inspecting it rather than cloning over it.

## 3. Install IBWD on the new device

From the new IBWD installation directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip check
.venv/bin/python -m ibwd.cli setup --help
.venv/bin/python -m ibwd.cli doctor --help
.venv/bin/python -m ibwd.cli usage-hook --help
```

Expected results:

- `pip check` reports no broken requirements.
- `setup --help` lists `--client`, `--repo` and `--dry-run`.
- `doctor --help` includes `--setup`.
- `usage-hook --help` succeeds.

If `setup` or `usage-hook` is missing, the source is too old. Obtain the current source using section 2 before continuing. Do not copy the original device's virtual environment; it must be created on the new device.

Define this convenience variable in your terminal. Adjust it if you chose a different installation directory:

```bash
IBWD_PY="$HOME/Tools/ibwdv1/.venv/bin/python"
"$IBWD_PY" -m ibwd.cli --help
```

This variable lasts for the current terminal session. Set it again in a new terminal, or use the interpreter's full path. The generated client settings contain absolute paths and do not depend on this variable or an activated virtual environment.

## 4. Install the coding clients, if needed

Skip installation for clients already installed. The commands below are the clients' official macOS/Linux installers:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
curl -fsSL https://claude.ai/install.sh | bash
```

Open a new terminal if prompted, then verify:

```bash
codex --version
claude --version
```

Sign in through each client's normal interactive flow when you launch it in section 6. Prefer your existing account arrangement; IBWD does not require API-key billing.

Official installation references: [Codex CLI](https://learn.chatgpt.com/docs/codex/cli), [Claude Code setup](https://code.claude.com/docs/en/setup). Use those pages for alternative installers or device-specific requirements.

## 5. Enable IBWD for a work repository

Clone or open the work repository on the **new device** first. Use its actual absolute root path, not the path from the original device:

```bash
IBWD_PY="$HOME/Tools/ibwdv1/.venv/bin/python"
TARGET_REPO="$HOME/Projects/my-project"
```

Replace `my-project` with your repository directory. Then run:

```bash
"$IBWD_PY" -m ibwd.cli setup --repo "$TARGET_REPO"
```

That one command configures both clients, installs routing guidance and automatic reporting, scans the repository, and checks local readiness.

Optional: preview the proposed file changes before setup:

```bash
"$IBWD_PY" -m ibwd.cli setup --repo "$TARGET_REPO" --dry-run
```

Optional: configure only one client instead of both:

```bash
"$IBWD_PY" -m ibwd.cli setup --repo "$TARGET_REPO" --client codex
```

Use `--client claude` for Claude Code only. The remainder of this guide assumes the default, both-client setup.

Setup manages these files in the work repository:

| File | Purpose |
| --- | --- |
| `.codex/config.toml` | Codex's project-specific IBWD server |
| `.mcp.json` | Claude Code's project-specific IBWD server |
| `AGENTS.md` | Codex's marked IBWD routing section |
| `CLAUDE.md` | Claude Code's marked IBWD routing section |
| `.codex/hooks.json` | Codex's automatic reporting hooks |
| `.claude/settings.local.json` | Claude Code's automatic reporting hooks |
| `.gitignore` | Ignore entries for local index, settings and backups |
| `.ibwd/` | Local index, reports and setup backups |

If root `AGENTS.override.md` already exists, setup puts Codex's routing section there instead of `AGENTS.md`. Existing unrelated instructions, settings, servers and hooks are preserved. Changed existing files receive first-install backups under `.ibwd/setup-backups/`.

Repeated setup does not duplicate identical hooks or routing sections. It updates the marked routing section and refreshes the index. Setup refuses malformed files, symlinked targets and conflicting existing IBWD server command/arguments before writing. Writes and indexing are not one all-or-nothing transaction; fix any reported failure and rerun.

Do not copy machine-specific `.codex/config.toml`, `.codex/hooks.json`, `.mcp.json` or `.claude/settings.local.json` from the original device. Generate them here. Portable routing instructions can be shared through Git. Ignore entries do not untrack files already committed.

## 6. Open and trust the integrations in each client

Start Codex from the work repository:

```bash
cd "$TARGET_REPO"
codex
```

Inside Codex:

1. Sign in if needed and complete the normal project trust flow.
2. Open `/mcp` and verify that `ibwd` is connected.
3. Open `/hooks`, review the IBWD `Stop` and `SessionEnd` command hooks, and trust them.

Start Claude Code in another terminal, or after leaving Codex:

```bash
cd /absolute/path/to/your/work-project
claude
```

Inside Claude Code, sign in if needed, complete the project/MCP approvals, and open `/mcp` to verify that `ibwd` is connected. Accept any applicable client hook/settings review prompts.

If either client was already open during setup, restart/reconnect it so it loads the new configuration and instructions. Do not bypass client trust or administrator policies.

References: [Codex MCP](https://developers.openai.com/codex/mcp), [Codex hooks and trust](https://learn.chatgpt.com/docs/hooks), [Claude Code MCP](https://code.claude.com/docs/en/mcp), [Claude Code hooks](https://code.claude.com/docs/en/hooks).

## 7. Work normally; reporting is automatic

You do not need to say “use IBWD” at the start of every session. The installed instructions tell both agents to use it first for supported discovery and relationship queries, including navigation during implementation, debugging and UI tasks. The server also supplies this guidance during MCP initialization.

The agents are instructed to scan before their first structural query, rescan after relevant changes, read source before editing, and explain when IBWD is unavailable or fails. Tool selection is still agent behavior, so verify it through ordinary work rather than assuming that installation guarantees adoption.

Reports update after normal assistant replies and normal session ends. Open these files in your editor:

```text
<work-repository>/.ibwd/usage/latest-codex.md
<work-repository>/.ibwd/usage/latest-claude.md
```

Individual session reports are retained under `.ibwd/usage/sessions/`. Resuming a session updates its existing report. No separate analysis command is needed.

The reports show recorded token usage, direct tool-call counts and available instruction evidence. They are snapshots: crashes, missing transcripts, child agents and indirect shell/orchestration calls can limit coverage. Claude instruction loading is not reconstructed. Zero recorded IBWD calls alone does not explain why it was unused, and token totals are not measured token savings.

Use your next actual coding task to observe adoption. Do not launch extra model sessions, paid pilots or benchmarks just to test IBWD.

## 8. Enable another repository

Reuse the same IBWD installation. Run this once for each additional repository:

```bash
IBWD_PY="$HOME/Tools/ibwdv1/.venv/bin/python"
"$IBWD_PY" -m ibwd.cli setup --repo "$HOME/Projects/another-project"
```

Then open the clients in that repository and complete their normal trust/reconnection steps. Each repository has separate server arguments, routing instructions, index and reports. Do not point a global fixed-repository server at every project.

“Any repository” means setup can target another local repository. Symbol/call-graph support currently covers production Python and JS/JSX/TS/TSX. Other languages still need ordinary text search. Test files, dynamic dispatch and inferred receivers are not fully represented; an empty graph result never proves that a symbol is unused or safe to delete.

## 9. Troubleshooting commands

These are occasional diagnostics, not a required per-session routine. Recreate `IBWD_PY` and `TARGET_REPO` in a new terminal before using them.

### Check setup and freshness together

```bash
"$IBWD_PY" -m ibwd.cli doctor --setup --repo "$TARGET_REPO"
```

Expected local status is `ready`. `runtime_connection` and `agent_adoption` remain `unverified`: this command checks disk configuration and index freshness, not a running client's trust, loaded tools or model behavior. Global policies and nested instruction overrides can still affect the client.

### Index is stale or missing

```bash
"$IBWD_PY" -m ibwd.cli scan --repo "$TARGET_REPO"
"$IBWD_PY" -m ibwd.cli doctor --setup --repo "$TARGET_REPO"
```

Avoid simultaneous scans from two clients. Scans are explicit, not continuous background monitoring.

### Configuration points to the old device or a moved installation

Print the correct configuration for the new paths:

```bash
"$IBWD_PY" -m ibwd.cli client-config --client codex --repo "$TARGET_REPO"
"$IBWD_PY" -m ibwd.cli client-config --client claude --repo "$TARGET_REPO"
```

In your editor, merge the generated IBWD entry into `.codex/config.toml` and `.mcp.json`, preserving other settings. Remove only obsolete IBWD `usage-hook` command entries from `.codex/hooks.json` and `.claude/settings.local.json` if their interpreter/repository paths changed. Otherwise a rerun may add the new command alongside the old command.

Then rerun `setup`, reconnect both clients, and review any changed hooks. Do not overwrite an entire shared client configuration with the printed snippet.

### IBWD is connected but unused

Check that the effective root instruction file contains the marked IBWD routing section. Rerun `setup` to restore/update it. Restart the client, check for nested instruction overrides, and inspect the next ordinary session's report. Distinguish unsupported tasks from missing tools or ignored routing guidance.

### Reports do not appear

Confirm the client loaded the project hooks. In Codex, check `/hooks` trust. Confirm hooks are not disabled by project/global/administrator settings, and that the client retains readable local transcripts. A report is first produced when an applicable event runs; setup itself does not create a fake session report. Inspect client hook errors if reporting still fails.

### `No module named ibwd`

Use the installed interpreter explicitly:

```bash
"$HOME/Tools/ibwdv1/.venv/bin/python" -m ibwd.cli --help
```

If that fails, reinstall from the installation directory:

```bash
cd "$HOME/Tools/ibwdv1"
.venv/bin/python -m pip install -e .
```

## 10. Update IBWD later

For a Git-based installation with no uncommitted local changes:

```bash
cd "$HOME/Tools/ibwdv1"
git status --short
```

If the worktree is clean, update:

```bash
git pull --ff-only
.venv/bin/python -m pip install -e .
.venv/bin/python -m ibwd.cli setup --repo /absolute/path/to/work-project
```

If the worktree has changes, preserve/reconcile them before updating. Repeat project setup for each repository to refresh routing and indexes, then reconnect clients. New or changed Codex hooks may require review again.

For an archive installation, `git pull` is unavailable. Obtain an updated source archive, install it into a new empty directory with its own environment, then follow the moved-installation instructions above for each work repository. Do not copy an environment from another device.

## Quick reference after installation

```bash
# Once for each repository: both clients by default.
"$HOME/Tools/ibwdv1/.venv/bin/python" -m ibwd.cli setup --repo /absolute/path/to/repo

# Only when troubleshooting: combined read-only check.
"$HOME/Tools/ibwdv1/.venv/bin/python" -m ibwd.cli doctor --setup --repo /absolute/path/to/repo
```

Normal routine: open your repository in Codex or Claude Code, do your work, and read the automatically updated report when useful.
