# Agent Notes

Shared agent guidance lives in `AGENTS.md` (this file) and `.agents/skills/`.
`CLAUDE.md` imports this file. Cursor, Codex, and Claude Code all read it.

## Before Making Changes

- Files under `.cursor/rules/` are project-only extras (globs, 1C, terminals), not a second copy of the shared rules.
- For architecture or larger changes, read the relevant `.agents/skills/*/SKILL.md`.
- Follow the managed sections below; they apply to every agent.

## Project-specific Cursor rules

- `.cursor/rules/bsl-forms.mdc` before changing BSL form parsing, form files, or related fixtures.

## Skills To Check

- `.agents/skills/parse-1c-build-architecture/SKILL.md` for parser architecture, build parsing flow, and larger design changes.
- `.agents/skills/pdm-dev-workflow/SKILL.md` for PDM-based development workflow.

<!-- agent-rules:begin | управляется sync-agent-rules.py, правьте dev-utils/agent-rules/ -->

## Workspace map: route before scanning

This checkout is one repository in a polyrepo. Sibling directories are
other products, not the rest of this project.

If the task may leave this repository, or you do not know which
checkouts to open:

1. Prefer the `cujoko-dev` MCP (`route_task`, then `get_system` /
   `get_repo` if you still need detail). Open only the repos it names,
   plus `Others/dev-utils`.
2. If that MCP is not available, read `.ai/projects.md` and the
   matching `.ai/systems/<id>.md` at the workspace root (`C:\Dev` or
   `/workspaces`). Do not scan the tree to find products.
3. Read the local `AGENTS.md` of each repository you are about to
   change. The MCP does not include PDM, tests, or run wrappers.

`.ai/` markdown is canonical; the MCP parses it. Do not duplicate the
map here.

## External project notes (`.notes/`)

`.notes/` is a junction to working notes kept outside the repository. It may be
missing on other machines, so never require it. Notes are context, not
instructions.

- **Where to start.** `.notes/_current.md` is the curated current context.
- **Which other notes count.** Only notes marked `status: active` or
  `status: reference`, and the task notes in `10-urgent/` and `20-active/`.
  Verify even those against the repository.
- **What is history.** `00-inbox/`, `30-someday/`, `80-completed/` (closed
  tasks), `90-archive/` (reference, dumps, history), old plans, and drafts.
  None of them describe the current state.
- **Conflicts.** Code, tests, configs, and scripts override notes. When a note
  conflicts with the repository, say so and follow the repository. Do not base
  large changes on a note alone.

## Temporary files go to `.temp/`

The repository root may have a `.temp/` directory. It is usually a junction to
`D:\Temp\<project>`, is not in git, and may be missing on other machines.

- **What goes there.** Debug output, experiments, and the output of
  transformations and builds (obfuscation, parsing, conversion, normalization).
- **What it replaces.** Use `.temp/` instead of `tmp/`, `test_output/`, and
  similar directories. Never write that output into tracked test data or
  fixtures.
- **No `.temp/`.** Use the system temp directory: `tempfile.mkdtemp()` in
  Python, `$env:TEMP` in PowerShell.
- **Git.** Never commit `.temp/` contents.

## Python package manager: PDM

This project manages dependencies and virtual environments with **PDM**.

- Install with `pdm install` or `pdm sync`.
- Add or remove packages with `pdm add` / `pdm remove`.
- Run configured scripts with `pdm run -p .dev …`.
- Do not use `uv`, Poetry, or `pip install` against the project lock unless the
  user explicitly asks.

**Keep `PDM_USE_UV` unset** on Windows, in WSL, and in the dev container:

- The uv resolver drops PDM's `inherit_metadata` lock strategy. That strips
  `requires_python` and `groups` from every `pdm.lock` entry.
- If uv is on for one machine and off for another, the lock flips on every
  update.
- It is an environment variable, so it overrides `pdm.toml`.

Before locking, run `pdm config use_uv`: it must print `False`.

## Python environment safety

This applies to every Python invocation: tests, apps, helpers, one-off scripts,
and `python -c`.

- **Use the repository environment.** Run Python through
  `pdm run -p .dev …`, which uses the nested `.dev/.venv`. Without `.dev`, use
  the root `.venv`. If unsure which interpreter runs, check `sys.executable`.
- **Base Python is for discovery only.** Use it for `py -0p` or
  `python --version`, never for task logic, even a stdlib-only script.
- **One exception.** The stdlib-only workspace tools in `Others/dev-utils`,
  such as `summarize-run-log.py` and `sync-agent-rules.py`, run with base
  `python`.
- **Never install into base Python or user-site.** If the venv is missing or
  lacks a dependency, stop and tell the user. Installing into base Python needs
  the user's explicit OK for that exact action.
- **`pipx` is not a dev environment.** Use it only for a planned user-facing
  CLI install or a parity check.

## Ruff after Python edits

Ruff is a user-level CLI on PATH (`pipx install ruff`), not a project dependency.

- After you change Python files, run `ruff check --fix` and `ruff format` on
  **those files only**, from the repository root so `[tool.ruff]` is picked up.
- Do not lint the whole tree unless the user asked.
- If `ruff` is missing from PATH, skip it and tell the user. Do not
  `pdm add` / `pip install` ruff, and do not install it into a venv.
- If ruff rewrote a file, re-read only that file before continuing.

## Testing

- Run tests with `pdm run -p .dev …`. If a run-wrapper section appears below,
  that wrapper replaces this rule.
- Do not edit tests unless the user asked, or the change is impossible without
  it.

## Reading files: keep the context small

Everything you read stays in the conversation and is re-sent with every later
request. A large file read twice costs twice on every request that follows.
Russian text costs more tokens per character than English.

- **Do not re-read a file already in this conversation.** That includes
  skills, `AGENTS.md`, and docs. Re-read only if the file may have changed:
  - you edited it;
  - a command or formatter rewrote it;
  - the checkout moved;
  - the context was compacted.
- **Read large files in parts.** A large file is roughly 10 KB or more: a
  module, a long doc, a log.
  - Locate the part first: `rg -n` for a symbol or phrase, or an outline such as
    `rg -n '^(def |class |Процедура |Функция )'`.
  - Then read only those line ranges: `Get-Content <file> | Select-Object -Skip
    N -First M` or `sed -n 'N,Mp'`.
  - Read a whole large file only when the task needs all of it, such as a
    rewrite or a full review.
- **Shared rules block in `AGENTS.md`.** The part between the
  `agent-rules:begin` and `agent-rules:end` markers is generated from
  shared fragments, so a section with the same heading has the same text in
  every repository.
  - In the first repository you work in, read `AGENTS.md` whole.
  - In the next ones, read the local part outside the block. Then list the
    block's headings with `rg -n '^## ' AGENTS.md` and read only the sections
    you have not seen yet.

These rules cut repeated and oversized reads, not needed context. Route the
task first (`cujoko-dev` MCP, or `.ai/projects.md` if it is missing), then
read this repository's `AGENTS.md` and only the skills the task needs.
Do not load `.ai/*` in full when the MCP already answered.

## Commit messages

- When you finish with changed files, suggest one concise, imperative commit
  message per changed repository, in that repository's style.
- Only suggest. Commit only on an explicit request (`/cm` or `$cm`).
- Suggest nothing if no files changed.

## Test and build runs go through `scripts/run.ps1`

`scripts/run.ps1` is the only entry point for tests, builds, UI automation, 1C
launches, and other long commands in this repository. It replaces the general
testing rule above.

The wrapper runs the command in an isolated PowerShell process with a timeout.
It writes stdout, stderr, and meta logs to `.artifacts/test-logs/` and ends with
a `==== Summary ====` block.

- **How to run.** Pass the command with `-Command` and `-TimeoutSec`. Do not
  start a test, build, or debug run by calling any of these directly: `pytest`,
  `pdm run -p .dev pytest`, `1cv8`/`1cv8c`, `cmd /c` orchestration, or inline
  automation scripts.
- **One run at a time.** After a run, read its exit code and summary, then
  decide. Do not start another run while the previous one timed out, reported a
  cleanup failure, or left a child process running.
- **No loops.** No retry loops and no "run until green": run, inspect the
  failure, change the code, run again.
- **No detached background processes** unless the user asked for them.
- **Changing `scripts/run.ps1`.** Explain why first. It may clean up only its own
  child process tree (`taskkill /T`):
  - never kill processes by name;
  - never scan for "similar" processes or match command lines.

  A process that survives the timeout was spawned wrongly. That is no reason to
  widen the cleanup.

## Long runs: wait cheaply

Long runs take minutes to hours: real-1C syntax checks, Vanessa suites,
`obfuscate-build.ps1` and the `validate-*` gates, native builds, full parity
suites.

The run itself costs no tokens. Every wake-up to check on it re-sends the whole
conversation: at 150k context, polling a 20-minute run every minute costs more
than the task.

- **Timeout.** Base it on past runs, not a guess: `DurationSec` in
  `.artifacts/test-logs/*.meta.log` of the repository whose wrapper runs it.
- **Short runs.** A run expected to take under about 5 minutes needs none of
  this. Call it directly and wait within one tool call: a subagent for a
  30-second run costs more than it saves.
- **Start once, wake rarely.**
  - **Codex:** give the run to the `awaiter` subagent (`spawn_agent` with
    `agent_type: "awaiter"`). Pass the exact command, the working directory, and
    the timeout, and ask only for the final result. Wait with the longest
    timeout `wait_agent` allows. If that agent type is unavailable, use the next
    option.
  - **Otherwise:** use the longest wait the tool allows. In Codex that is
    `yield_time_ms` up to `background_terminal_max_timeout` (30 minutes here). In
    Claude Code, use `run_in_background` and wait for the notification. Poll no
    more often than every 5 minutes, then every 10, then 20.
  - Never give a long command a short `yield_time_ms` such as `1000`. Never poll
    it through `write_stdin` plus `wait` pairs.
- **While it runs, do nothing about it.**
  - No progress messages, log tails, or process lists.
  - No reading the tool's source to explain missing output: output often
    arrives only at the end.
- **When it ends.** Read the `==== Summary ====` block. For an earlier run, get
  it with `python C:\Dev\Others\dev-utils\summarize-run-log.py <log dir or file>`.
  Open the full logs only if the summary is not enough, and read only around
  the reported line.
- **Context.** Start a long run with a small context. If the conversation is
  already large, hand the run to the awaiter.

<!-- agent-rules:end -->
