# Retro Engine design

Date: 2026-11-14. Target version: 0.9.x (built on the 0.8.4 branch).

## Goal

The factory learns from its own sessions, on every project it runs on this machine, not inside one project only. Once a day, a Retro agent looks at what happened since the last retro (errors, hook blocks, approval prompts, time and calls per dispatch, the user's corrections), finds the top frictions, and drafts the fix. Lessons seen across several projects are proposed upstream as a draft pull request.

This extends the manual Retro step in `agents/team-lead.md` (Build loop step 12). It does not replace it.

## Scope

- Reads only sessions in **factory projects**: a project whose working directory holds `docs/factory/status.md`. Worktree sessions (`.claude/worktrees/*`, `.worktrees/*`, sibling `worktrees/*`) are grouped under their parent repo.
- Non-factory sessions are never read.
- Read-only on code. Never edits plugin files, project code or `~/.claude/settings.json`.

## Components

### 1. `scripts/retro-scan` (deterministic, no model tokens)

Python, standard library only. Inputs: Claude Code transcripts under `~/.claude/projects/*` (including `subagents/*.jsonl` and `*.meta.json`).

- `retro-scan --discover`: list factory projects and their worktrees.
- `retro-scan --due`: for each project, compare `git ls-remote origin main` with the last sha a retro covered (from `retro-metrics.jsonl`). A project is due when main moved **and** it has 2 or more sessions in the window. Exit 0 with an empty list when nothing is due.
- `retro-scan --project <path> [--since <iso>] [--branch <name>] --out <file>`: measure one project. Default window is since that project's last retro.
- `retro-scan --due-out <dir>` writes `due.json` for an on-demand run, only for a project that `--discover` finds.

Measures, per project and per role:
- tool errors by class and by tool, inside and outside subagents;
- hook blocks by hook and reason;
- sandbox-off Bash calls by command shape, `ask`-rule hits, classifier denials, user rejections;
- GitHub writes by approval path (sandbox exclusion, allow rule, prompt);
- per dispatch: agent type, model, minutes (median, p90), tool calls, errors;
- user messages sorted into correction, preference and praise (keyword rules, no model);
- PR review comments and CI run durations via `gh` (marked `n/a` if `gh` fails).

Output: one JSON file with a hard size cap (top-N lists, at most 40 sampled user messages, each truncated).

### 2. `agents/retro.md` (one `sonnet` run per due project)

Inputs: the scan JSON for the project, the previous retro's changes, and the current overlay.

Steps:
1. **Close the loop first.** For each change the previous retro made, read its proof metric and mark it `kept` or `reverted`. A reverted entry that went live on its own is retired. A reverted approved entry gets `action: ask-retire`, and the user decides. A metric that is 0 before and 0 now reads as kept.
2. Find the top 3 frictions per project, blaming the brief or setup, not the model.
3. Propose at most 2 changes per retro. Each names the owner role, the asset (rule, hook, check, script or eval) and the metric that proves it worked.
4. Classify each change as **preference** or **improvement** (see Overlay), and as **project**, **user**, **machine setup** or **plugin** level.
5. Check each candidate entry with `retro-check apply`. Every accepted entry waits for the user (see Autonomy).
6. Write the report in `work/`. `retro-check publish` copies it into the project and writes the history row.

### 2b. `scripts/retro-check` (deterministic, the only writer)

Subcommands: `apply`, `check` (read-only, used by `review`), `approve`, `drop`, `retire` and `repair` (user only, refused when `RETRO_HEADLESS=1`), `verdicts`, `lessons`, `scrub [--strict]` and `publish`.

`publish` requires: the report sits in `work/`; `--metrics` is the launcher's scan; `--project` is in `due.json` and matches the scan; lesson keys match `[a-z0-9-]{1,40}`; the strict scrub passes (minus the project's own name); and no run of 8 or more words is copied from raw user or PR comment samples.

### 3. Scheduler (daily)

Installed by factory setup, after the user agrees:
- macOS: a launchd plist in `~/Library/LaunchAgents/`.
- Linux: one crontab line.

The job runs `retro-scan --due` once a day, then one headless `claude -p --agent software-factory:retro` run per due project, at most 5 a day. When main has not moved in any project, no model is called. The launcher takes the plugin only from the `software-factory-marketplace` cache and refuses a project that is the home folder or above it.

Headless permissions: `--permission-mode dontAsk`, `--setting-sources project`, `--strict-mcp-config`. Disallowed: WebFetch, WebSearch, Agent, `mcp__*`, secret folders and secret file globs (`.env*`, `.npmrc`, `.netrc`, `.pypirc`, `id_*`, `*.p12`). Read rules cover the run folder, `overlay/`, `upstream/`, the plugin and the project. Edit rules cover `work/` and `upstream/`. Bash is limited to `retro-check apply|verdicts|lessons|scrub|publish`. The launcher sets `RETRO_HEADLESS=1`.

Cloud scheduled agents are not used: they cannot read local transcripts.

### 4. On demand

`/software-factory:retro [project] [--branch <name>] [--since <date>]`, or the user asking the Team Lead for a retro. It runs whether or not main moved, so it covers work that never reached main (for example, a dev branch that is stuck before merge). It runs inline in the user's session (no nested headless run), following `agents/retro.md`, in its own folder `<state>/retros/<day>/<slug>-now/`, so it never overwrites a daily run.

### 5. Overlay (where fixes land)

Plugin files are never edited locally: the plugin cache is overwritten on update. Three layers, read in this order:

| Layer | Path | Written by | Changes allowed |
|---|---|---|---|
| Plugin | `${CLAUDE_PLUGIN_ROOT}` | upstream PR only | any, through review |
| User overlay | `<state>/overlay/` (`entries.jsonl`, `pending.jsonl`) | `retro-check` only | add, tighten, pref |
| Project overlay | `docs/factory/brain.md`, `docs/architecture/project-facts.md` | Team Lead (Retro feeds it) | add, tighten, pref |

`<state>` is `$SOFTWARE_FACTORY_HOME`, else `~/.software-factory/`. It also holds `retros/`, `upstream/`, `retro-metrics.jsonl`, `retro-settings.json`, `retro-failed`, `retro.log` (rotated at 1 MB), `private-words.txt` and `bin/retro-daily`.

Who writes what: the Retro agent writes only in `<run>/work/` and `<state>/upstream/`. The scripts write everything else (`due.json`, the scan files, the overlay, the history). `retro-check` is the only writer of the overlay and the history. `retro-check publish` copies the report into the project after the scrub check. The `project_bounds` hook allows `work/` and `upstream/` for the `retro` agent only.

Run folders are per project: `<state>/retros/<day>/<slug>/` (daily, with its own `due.json`) and `<day>/<slug>-now/` (on demand).

Project lessons: brain notes are listed in the report, and the Team Lead adds them to `brain.md` at the start of the next request (the brain keeps one owner).

Each overlay entry records: `id`, `kind` (`add`, `tighten`, `pref`), `role`, `text`, `target` (plugin file and heading), `anchor_sha` (hash of the target section's normalized text), `plugin_version`, `evidence` (metric and value), `class` (`preference` or `improvement`), `status` (`active`, `stale`, `retired`, `pending`), `reason`, `went_live_at` and `went_live_by`.

- An entry may add or tighten a rule, or set a value for a setting the factory already has. It may **never** loosen a contract safety rule: the approval gates, rule 9.13, any `SR-NN`, the section 8 review rules. A check rejects a loosening entry, not the model's judgment.
- The check fails closed, with three answers: `accept`, `ask` or `reject`. An entry that is not plainly harmless (protected words, role names, non-ASCII text, the security role, any `pref`) gets `ask`. Text is capped at 400 characters and 3 lines, and may not start as a list, heading or quote. Bidi and other control characters are refused.
- The overlay is capped at about 100 lines in total. Over the cap the new entry is rejected ("retire an entry first").
- A SessionStart hook prints the overlay into the main session. The Team Lead passes each role's lines in the brief. The hook also lists entries that went live in the last 7 days, and any active row `retro-check` did not make, each with a `retro-check retire <id>` revert command.
- **After a plugin update**, the same hook re-hashes each `target`:
  - hash unchanged: keep the entry;
  - the plugin now contains the lesson: retire the entry;
  - the plugin reversed the rule, or the heading is gone: mark it `stale` and stop applying it; the next retro asks the user keep, drop or rewrite;
  - the entry now breaks a contract limit: disable it and report it.

### Preference vs improvement (users who learn opposite lessons)

- A **preference** is a value for a setting that already exists (Pace, Security per epic, roster, model choice). It stays in the overlay and is never proposed upstream. "Skip security" can only be set through the existing per-epic setting, with an ask.
- An **improvement** fixes something measurable: an error, a hook block, a repeated prompt, a re-dispatch, a repeated correction.
- When two users or projects learn opposite lessons, the upstream proposal is a new setup option with a safe default, never a rule.

## Autonomy

Draft only in 0.9.x. Setup writes `{"mode": "draft"}` and asks nothing. The retro writes the report and the drafted changes. Every accepted entry waits for the user's yes (`retro-check approve`, or `drop`). The auto-fix code path stays for a later release, but its heading allowlist is empty, so it applies nothing.

The retro always asks before any change that:
- touches security (auth, secrets, sandbox, permissions, allow rules, hooks);
- lowers a review through an existing setting (for example the Security roster);
- writes outside the overlay or the project brain.

Overlay entries can never loosen a rule. Allow rules always touch security, so they always wait for the user.

Machine-level frictions (for example, a shell wrapper hook that rewrites commands so they miss the sandbox exclusion) are reported as a **user setup** finding with the exact fix for the user to apply. The retro never applies them.

## Upstream (plugin-level lessons)

A lesson becomes an upstream candidate only when all hold:
- seen in 3 or more projects;
- a measured gain over 10 or more dispatches (the scan does not count review findings, so the report says when it could not check them);
- it contradicts no contract rule;
- it is an improvement, not a preference.

The retro then writes a scrubbed draft file in `<state>/upstream/`. A draft that fails the scrub is replaced by one starting `withdrawn: scrub hits`. `/software-factory:retro review` runs `retro-check check` on the draft text, then turns it into a branch and draft pull request after the user's yes. Nothing is pushed or opened before that. The report lists pending drafts.

## Privacy

- Raw quotes and message samples stay in `<state>/retros/` (machine-local, outside every repo).
- The project report `docs/factory/retros/` holds counts and paraphrases only, with no links or images.
- Anything bound upstream passes a scrub step (no project names, paths, people, customers or quotes; counts and generic wording only), then a deterministic check: the user's project names (from `--discover`) and `private-words.txt`, plus secret, email and path patterns. A hit blocks the draft. The message names the rule and line, never the match.

## Outputs

- Project report: `docs/factory/retros/<date>-<sha7>.md`: metrics table, previous changes kept or reverted, top 3 frictions, at most 2 changes (owner, asset, proof metric), user setup findings, pending upstream drafts.
- Cross-project history: one row per project per retro in `<state>/retro-metrics.jsonl` (`project`, `sha`, `window`, headline metrics, changes and their status).
- Private samples: `<state>/retros/<date>/<slug>/`.

## Cost

- One `sonnet` run per due project, at most 5 a day.
- Input capped by the scan's size limit; at most 2 changes per project per retro.
- No run when nothing is due.

## Errors

- A scan that fails on one project: skip it, log it in the report, continue.
- `gh` unavailable: PR and CI metrics are `n/a`.
- A failed headless run writes a marker in `<state>/retro-failed`: fixed status words, an exit code and a project slug, never child output. The full output goes to `retro.log`. The next SessionStart tells the Team Lead `retro failed: <reason>`.
- Every writing `retro-check` command holds `<state>/overlay/.lock`, which the SessionStart refresh shares. A corrupt overlay file is copied to `*.corrupt-<time>` before `repair` rewrites it. Errors exit 2 with a JSON message, never a traceback.

## Testing

Unit tests in `tests/`, in the existing style, on fixture transcripts:
- the scan surfaces known root causes planted in fixtures: a wrapper that turns `gh ...` into another command and so hits sandbox retries; `cd X && ...` chain blocks; scratch-script commands that never match an allow rule;
- `--due` skips a project whose main has not moved and one with fewer than 2 sessions;
- worktree sessions group under their parent repo;
- an overlay entry that loosens a contract rule is rejected;
- the upstream scrub check catches a planted project name and home path;
- the overlay update check marks an entry stale when its target heading changed.

## Decisions

- **Show and revert (#161).** SessionStart lists what went live in the last 7 days, each with a revert command. Auto-fix may only touch headings on an allowlist.
- **Empty allowlist (#174).** The allowlist ships empty, so every fix waits for the user's yes. The setup question is dropped and mode is always `draft`. The auto-fix code path stays for a later release.
- **Anchor hash.** The first draft said the hash covers the heading's text. The code hashes the whole target section, lowercased with whitespace collapsed, and keeps 16 hex characters. A whitespace-only plugin edit does not make an entry stale.
- **0 == 0 reads as kept.** A metric that is 0 before and 0 now counts as kept, not reverted.
- **One writer.** `retro-check` alone writes the overlay and the history.
- **`RETRO_HEADLESS` is a seat belt.** It makes user-only commands refuse. The permission flags are the control.
- **State folder.** Default `~/.software-factory`, because Claude Code refuses writes under `~/.claude`.
- **Per-project run folders.** One folder per project per day, so a run reads and publishes for its own project only.

## Not checked yet

- Checked 2026-11-14 (spike, macOS, scheduler-like launch: `env -i` with only HOME, USER, LOGNAME, SHELL, TMPDIR and a minimal PATH): `claude -p --agent software-factory:<name>` runs headless, loads the plugin agent and needs no prompt. Findings that shape the launcher:
  - Auth reads the login keychain and fails with "Not logged in" unless USER (and LOGNAME) are set. A bare HOME and PATH is not enough. `retro-daily` inherits the environment, and the plist sets only PATH (and `SOFTWARE_FACTORY_HOME`). launchd normally supplies USER and LOGNAME, but no run under launchd or cron has been recorded yet.
  - The flag is `--allowedTools` (alias `--allowed-tools`); the permission mode flag is `--permission-mode`. The flag takes a variadic list, so the prompt must come before it (`claude -p "<prompt>" --agent ... --allowedTools "..."`), or it swallows the prompt and fails with "Input must be provided".
  - Rules are comma separated. `Bash(python3 <abs path>/scripts/review-route *)` allowed the script. `Write(<path>/**)` is rejected: only `Edit(<path>)` rules match file writes (they cover all file-editing tools). An absolute path needs a leading double slash: `Edit(//<abs path>/**)` allowed the write, `Edit(<abs path>/**)` did not.
  - A write outside the rule was denied with "you haven't granted it yet", and no file was created.
  - The agent's own tool list still applies: `triage-classifier` has no Write tool and refused. The retro agent must list Write and Bash in its frontmatter.
- Dry run 2026-11-15 (scratch state folder, haiku, no publish, nothing written to a project):
  - `retro-scan --due` listed 1 project.
  - A scan of a worktree path found 0 sessions; a scan of the main checkout path found 19 sessions, 3168 tool calls, 370 errors, 127 hook blocks, 86 sandbox-off calls, 24 ask hits, 48 corrections, 59 dispatches.
  - Headless `retro-daily` for 1 project: exit 0, no `retro-failed`, 0 permission-denied lines, 3 files written in `work/` (report, verdicts, changes). The agent skipped publish because the test prompt had no sha (the test stub left it out).
  - In-session flow: `retro-scan`, `retro-check verdicts` and `retro-check lessons` ran as written. Writing `due.json` with the Write tool was refused by the factory write hook, because the dry run ran as a subagent. `--due-out` now writes it.

## Not done

- Re-checking active entries against a changed contract floor after an update (the floor lives in the check, not in plugin text).
- Mapping removed worktrees outside `.claude/worktrees` and `.worktrees`.
- Repositories whose default branch is not `main`.

## Out of scope

- Phoenix traces, `~/.claude/permission-review/friction.json` and other machine-specific sources: possible later inputs, not in 0.9.x.
- Non-factory sessions.
- Automatic push or merge of anything.
