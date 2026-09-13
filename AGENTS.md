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

## External project notes

This project may have a `.notes` directory that points to external working notes.

Rules for using `.notes`:

- `.notes` is not automatically authoritative.
- Prefer `.notes/_current.md` as the curated current context.
- Treat other notes as non-authoritative unless they have explicit metadata such as `status: active` or `status: reference`.
- Treat `.notes/00-inbox/`, `.notes/30-someday/`, `.notes/80-completed/`, `.notes/90-archive/`, old plans, drafts and raw imported notes as historical or unprocessed context only.
- Folders `.notes/10-urgent/` and `.notes/20-active/` may hold current task notes; still verify them against the repository before acting.
- Closed tasks live in `.notes/80-completed/`; reference, dumps and historical material live in `.notes/90-archive/`.
- Source code, tests, configs, migrations, build scripts and repository files override external notes.
- If an external note conflicts with repository files, do not silently follow the note. Mention the conflict and prefer the repository.
- Do not perform large changes based only on old notes. First verify against current code and current project instructions.

## Local junction directories

The project root on a developer machine may contain **junction** directories
(not in git; they may be missing on other machines).

### `.temp/`

- Temporary files **for this project**: debug output, intermediate artifacts,
  manual experiments.
- The junction points outside the repository (typically `D:\Temp\<project>`).
- If `.temp/` exists, prefer it over `tmp`, `temp`, `test_output`, and similar
  directories inside the tracked tree.
- If the junction is absent, use the system temp directory (Python:
  `tempfile.mkdtemp()`, `tempfile.TemporaryDirectory()`; PowerShell: `$env:TEMP`,
  `[System.IO.Path]::GetTempPath()`).
- Output of a transformation or build — obfuscation, parsing, conversion,
  normalization — goes there too. Never write it into tracked test data or
  fixtures.
- Do not commit `.temp/` contents.

### `.notes/`

- Local external working notes. The junction typically points outside the
  repository, for example `D:\Notes\Work\_Dev\...\<project>`.
- Policy for those notes is in the "External project notes" section.
- If `.notes/` is absent, do not require it in CI or on other machines.

## Python package manager: PDM

This project uses **PDM** for dependencies and virtual environments.

- Install or sync with `pdm install` or `pdm sync`.
- Add or remove packages with `pdm add` / `pdm remove`.
- Run tools and scripts with `pdm run -p .dev …` when they are configured in
  `pyproject.toml`.

Do **not** use `uv`, `pip install` (for project lockfiles), or Poetry unless the
user explicitly asks for an exception.

`PDM_USE_UV` must stay **unset** in every environment — Windows, WSL and the
dev container alike. The uv resolver does not support PDM's `inherit_metadata`
lock strategy and silently discards it. When that happens, `requires_python` and
`groups` disappear from every entry in `pdm.lock`, so the lock no longer records
which group a package belongs to. Updating a single package rewrites roughly
600 lines.

A mixed setup is the worst case: with uv enabled on one machine and disabled on
another, `pdm.lock` flips between `strategy = ["inherit_metadata"]` and
`strategy = []` on every update, producing conflicts across the whole file.

`PDM_USE_UV` is an environment variable and overrides a per-project `pdm.toml`,
so the setting cannot be pinned inside the repository. Check before locking:

```sh
pdm config use_uv   # must report False
```

## Python environment safety

These rules apply to **every** Python invocation: tests, apps, helpers,
migrations, generators, one-off scripts, and `python -c`.

- Resolve the repository environment first and check the real interpreter with
  `sys.executable`.
- Prefer the nested `.dev` project (typically `.dev/.venv`) through the project
  manager: `pdm run -p .dev ...`. If `.dev` is absent, use the root `.venv`.
- Do not run task logic with system/base Python or user-site, even for a
  temporary script that only uses the standard library.
- Base Python is allowed only to discover interpreters and verify the
  environment (`py -0p`, `python --version`, printing `sys.executable`). After
  that, run further Python through the project environment.
- Do not install dependencies into system/base Python or user-site
  (`pip install`, `python -m pip install`, and equivalents aimed there).
- If there is no suitable venv, or a dependency is missing from it, stop and
  tell the user. Do not "fix" that with a global install.
- Installing or upgrading anything in base Python needs an explicit user OK for
  that exact action.
- Use `pipx` only for a planned user-facing CLI install or parity check, never
  as a substitute for the repository dev environment.

## Environment and testing

- Run Python and tests with `pdm run -p .dev ...`. Do not activate the venv by
  hand. If a section below names a run wrapper for this repository, that wrapper
  is the only entry point and overrides this line.
- Do not edit test files unless the user asked, or the change is impossible
  without touching tests.

## Reading files: keep the context small

Whatever you read stays in the conversation and is sent again with every later
request. A large file read twice costs you twice on every request that follows.
Russian text also costs more tokens per character than English.

- **Do not re-read a file already in this conversation.** Use the content you
  already have. Re-read only when the file may have changed since:
  - you edited it;
  - a command or formatter rewrote it;
  - the checkout moved.

  After the context has been compacted, the earlier content is gone, so reading
  it again is correct. The same applies to skills, `AGENTS.md`, and docs.
- **Read large files in parts.** A large file is roughly 10 KB or more: a source
  module, a long doc, a log.
  - First find the place: `rg -n` for a symbol or phrase, or an outline such as
    `rg -n '^(def |class |Процедура |Функция )'`.
  - Then read only the line ranges you need, for example
    `Get-Content <file> | Select-Object -Skip N -First M` or `sed -n 'N,Mp'`.
  - Read a large file whole only when the task needs all of it, such as a
    rewrite or a full review.

These rules are about repeated and oversized reads, not about skipping context.
The start-up route (`AGENTS.md`, `.ai/*`, the relevant skills) is still read
once.

## Commit message format

When the agent stops and has changed files, it must proactively suggest commit
messages.

For each affected project/repository with file changes, provide exactly one
ready-to-use message.

If no files were changed, do not suggest a commit message.

Each message must be concise, imperative, and aligned with repository style.
This is a suggestion only. Creating a commit is a separate explicit request
(`/cm` or `$cm`).

## Test and build runs go through `scripts/run.ps1`

This repository has a wrapper at `scripts/run.ps1`, and it is the only entry
point for a test, build, UI automation, 1C launch, or any other long-running
command. Where this section is present it **overrides** the general testing
rule above: the wrapper wins over calling the test runner yourself.

- Do not run these directly for a test, build, or debug workflow: `pytest`,
  `python -m pytest`, `pdm run pytest`, `pdm run -p .dev pytest`, `1cv8`,
  `1cv8c`, PowerShell holding inline automation logic, `cmd /c` used to
  orchestrate tests, or any retry loop in the terminal.
- Direct terminal commands are limited to safe read-only inspection:
  `git status`, `git diff`, `git log --oneline -n 20`, `rg`, `Get-Content`,
  `ls`, `dir`.
- Pass the target test command to the wrapper through its parameters.
- After each run: inspect the exit code, then the log it produced, and only
  then decide the next step.
- Do not start a new run while the previous one reported a timeout, a cleanup
  failure, or a still-running child process.
- Do not spawn background processes unless the user asked for them.
- No "run until green" loop: run once, inspect the failure, change the code,
  run again.
- Explain why before changing `scripts/run.ps1`.

### What the wrapper may and may not do

It runs a command in an isolated PowerShell process, captures stdout and stderr
into log files, enforces a timeout, and on timeout terminates **only** the child
process tree (`taskkill /T`).

- It must never clean up globally: no killing by process name (`python`,
  `node`, `1cv8`), no scanning the system for "similar" processes, no
  command-line pattern matching to pick kill targets, no heuristic cleanup
  outside its own process tree.
- Only processes the wrapper started may be managed by it.
- A process that survives the timeout is a bug in how it was spawned — not a
  reason to widen the cleanup.

## Long runs: wait cheaply

Real-1C syntax checks, Vanessa suites, `obfuscate-build.ps1` and the
`validate-*` gates, native builds, and full parity suites run for minutes to an
hour. The run itself costs no tokens. Every time you wake up to check on it,
the whole conversation is sent again. At 150k tokens of context, polling a
20-minute run every 50 seconds costs more than the rest of the task.

- Before starting, look up how long similar runs took: `DurationSec` in
  `.artifacts/test-logs/*.meta.log`. Set the timeout from that, not from a guess.
- Start the run once, then wait in as few wake-ups as possible:
  - **Codex:** hand the run to the `awaiter` subagent (`spawn_agent` with
    `agent_type: "awaiter"`). Give it the exact command, the working directory,
    and the timeout, and ask only for the final result. Its context is small,
    so its polls are cheap. Wait for its answer with the longest timeout
    `wait_agent` allows.
  - **Without a subagent:** give the command the longest wait the tool allows.
    In Codex, that is `yield_time_ms` up to `background_terminal_max_timeout`
    (30 minutes on this workstation). In Claude Code, run the command with
    `run_in_background` and wait for the completion notification; that is a
    tracked tool call, not a detached background process. If the run is still
    going, poll at growing intervals (5, 10, 20 minutes), never more often than
    every 5 minutes.
  - Never give a long command a short `yield_time_ms` such as `1000`, and never
    poll it through a `write_stdin` plus `wait` pair.
- While the run is in progress, do nothing about it:
  - no progress messages;
  - no log tails or process lists;
  - no reading the tool's source to explain missing output.

  Output that appears only at the end is expected: child tools run with captured
  output.

- When the run ends, read the `==== Summary ====` block first. `scripts/run.ps1`
  prints it at the end of the run. For a finished run, get it again with
  `python C:\Dev\Others\dev-utils\summarize-run-log.py <log dir or log file>`.
  Open the full logs only if the summary is not enough. Then read a bounded
  slice around the reported line, not the whole file.
- Start a long run with a small context. Do not load whole files "just in case"
  beforehand. If the conversation is already large, hand the run to the
  awaiter.

<!-- agent-rules:end -->
