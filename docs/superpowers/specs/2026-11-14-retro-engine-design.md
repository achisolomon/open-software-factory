# Retro Engine design

Date: 2026-11-14. Target version: 0.9.x (built on the 0.8.4 branch).

## Goal

The factory learns from its own sessions, on every project it runs on this machine, not inside one project only. Once a day, a Retro agent looks at what happened since the last retro (errors, hook blocks, approval prompts, time and calls per dispatch, the user's corrections), finds the top frictions, and either fixes them (in the user's overlay) or drafts the fix. Lessons seen across several projects are proposed upstream as a draft pull request.

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

Measures, per project and per role:
- tool errors by class and by tool, inside and outside subagents;
- hook blocks by hook and reason;
- sandbox-off Bash calls by command shape, `ask`-rule hits, classifier denials, user rejections;
- GitHub writes by approval path (sandbox exclusion, allow rule, prompt);
- per dispatch: agent type, model, minutes (median, p90), tool calls, errors;
- user messages sorted into correction, preference and praise (keyword rules, no model);
- PR review comments and CI run durations via `gh` (marked `n/a` if `gh` fails).

Output: one JSON file with a hard size cap (top-N lists, at most 40 sampled user messages, each truncated).

### 2. `agents/retro.md` (one `sonnet` dispatch per run)

One dispatch covers every due project. Inputs: the scan JSON for each project, the previous retro's changes, and the current overlay.

Steps:
1. **Close the loop first.** For each change the previous retro made, read its proof metric and mark it `kept` or `reverted` (a reverted overlay entry is removed).
2. Find the top 3 frictions per project, blaming the brief or setup, not the model.
3. Propose at most 2 changes per retro. Each names the owner role, the asset (rule, hook, check, script or eval) and the metric that proves it worked.
4. Classify each change as **preference** or **improvement** (see Overlay), and as **project**, **user**, **machine setup** or **plugin** level.
5. Apply or draft according to the autonomy mode (see Autonomy).
6. Write the report and the metrics row.

### 3. Scheduler (daily)

Installed by factory setup, after the user agrees:
- macOS: a launchd plist in `~/Library/LaunchAgents/`.
- Linux: one crontab line.

The job runs `retro-scan --due` once a day. Only when a project is due does it start `claude -p --agent software-factory:retro` headless. When main has not moved in any project, no model is called.

Cloud scheduled agents are not used: they cannot read local transcripts.

### 4. On demand

`/software-factory:retro [project] [--branch <name>] [--since <date>]`, or the user asking the Team Lead for a retro. It runs whether or not main moved, so it covers work that never reached main (for example, a dev branch that is stuck before merge).

### 5. Overlay (where auto-fixes land)

Plugin files are never edited locally: the plugin cache is overwritten on update. Three layers, read in this order:

| Layer | Path | Written by | Changes allowed |
|---|---|---|---|
| Plugin | `${CLAUDE_PLUGIN_ROOT}` | upstream PR only | any, through review |
| User overlay | `~/.claude/software-factory/overlay/` (`roles/<role>.md`, `prefs.md`, `allowlist.md`) | Retro agent | add, tighten, pref |
| Project overlay | `docs/factory/brain.md`, `docs/factory/project-facts.md` | Team Lead (Retro feeds it) | add, tighten, pref |

Each overlay entry records: `id`, `kind` (`add`, `tighten`, `pref`), `target` (plugin file and heading), `anchor_sha` (hash of the target heading's text), `plugin_version`, `evidence` (metric and value), `class` (`preference` or `improvement`).

- An entry may add or tighten a rule, or set a value for a setting the factory already has. It may **never** loosen a contract safety rule: the approval gates, rule 9.13, any `SR-NN`, the section 8 review rules. A loosening entry is rejected by a check, not by the model's judgment.
- The overlay is capped at about 100 lines in total. Over the cap, the lowest-evidence entry is dropped first.
- A SessionStart hook loads the overlay into the agents' context.
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

Setup asks each user to choose a mode. Default is `draft`.

- `draft`: the retro writes the report and the drafted changes; nothing is applied.
- `auto-fix`: the retro applies overlay and brain entries itself.

In both modes, the retro asks before any change that:
- touches security (auth, secrets, sandbox, permissions, allow rules, hooks);
- loosens a contract rule;
- writes outside the overlay or the project brain.

Machine-level frictions (for example, a shell wrapper hook that rewrites commands so they miss the sandbox exclusion) are reported as a **user setup** finding with the exact fix for the user to apply. The retro never applies them.

## Upstream (plugin-level lessons)

A lesson becomes an upstream candidate only when all hold:
- seen in 3 or more projects, or for 2 or more users;
- a measured gain over 10 or more dispatches, with no rise in Blocker or Major findings;
- it contradicts no contract rule;
- it is an improvement, not a preference.

The retro then prepares a **draft pull request**: a local branch and PR text in a throwaway worktree of the plugin repo. Nothing is pushed or opened until the user says yes in a session. The report lists pending drafts.

## Privacy

- Raw quotes and message samples stay in `~/.claude/software-factory/retros/` (machine-local, outside every repo).
- The project report `docs/factory/retros/` holds counts and paraphrases only.
- Anything bound upstream passes a scrub step (no project names, paths, people, customers or quotes; counts and generic wording only), then a deterministic check: a list of the user's project names and home path built by `--discover`, plus email and path patterns. If the check finds a hit, the draft PR is blocked.

## Outputs

- Project report: `docs/factory/retros/<date>-<sha7>.md`: metrics table, previous changes kept or reverted, top 3 frictions, at most 2 changes (owner, asset, proof metric), user setup findings, pending upstream drafts.
- Cross-project history: one row per project per retro in `~/.claude/software-factory/retro-metrics.jsonl` (`project`, `sha`, `window`, headline metrics, changes and their status).
- Private samples: `~/.claude/software-factory/retros/<date>/`.

## Cost

- One `sonnet` dispatch per run, covering all due projects.
- Input capped by the scan's size limit; at most 2 changes per project per retro.
- No run when nothing is due.

## Errors

- A scan that fails on one project: skip it, log it in the report, continue.
- `gh` unavailable: PR and CI metrics are `n/a`.
- A failed headless run writes a marker in `~/.claude/software-factory/retro-failed`. The next SessionStart tells the Team Lead `retro failed: <reason>`.

## Testing

Unit tests in `tests/`, in the existing style, on fixture transcripts:
- the scan surfaces known root causes planted in fixtures: a wrapper that turns `gh ...` into another command and so hits sandbox retries; `cd X && ...` chain blocks; scratch-script commands that never match an allow rule;
- `--due` skips a project whose main has not moved and one with fewer than 2 sessions;
- worktree sessions group under their parent repo;
- an overlay entry that loosens a contract rule is rejected;
- the upstream scrub check catches a planted project name and home path;
- the overlay update check marks an entry stale when its target heading changed.

## Not checked yet

- Checked 2026-11-14 (spike, macOS, scheduler-like launch: `env -i` with only HOME, USER, LOGNAME, SHELL, TMPDIR and a minimal PATH): `claude -p --agent software-factory:<name>` runs headless, loads the plugin agent and needs no prompt. Findings that shape the launcher:
  - Auth reads the login keychain and fails with "Not logged in" unless USER (and LOGNAME) are set. A bare HOME and PATH is not enough. `retro-daily` must pass HOME, USER, LOGNAME.
  - The flag is `--allowedTools` (alias `--allowed-tools`); the permission mode flag is `--permission-mode`. The flag takes a variadic list, so the prompt must come before it (`claude -p "<prompt>" --agent ... --allowedTools "..."`), or it swallows the prompt and fails with "Input must be provided".
  - Rules are comma separated. `Bash(python3 <abs path>/scripts/review-route *)` allowed the script. `Write(<path>/**)` is rejected: only `Edit(<path>)` rules match file writes (they cover all file-editing tools). An absolute path needs a leading double slash: `Edit(//<abs path>/**)` allowed the write, `Edit(<abs path>/**)` did not.
  - A write outside the rule was denied with "you haven't granted it yet", and no file was created.
  - The agent's own tool list still applies: `triage-classifier` has no Write tool and refused. The retro agent must list Write and Bash in its frontmatter.
- How a SessionStart hook can best load the overlay into subagent context (the hook output reaches the main session; subagents may need the Team Lead to pass it in briefs).

## Out of scope

- Phoenix traces, `~/.claude/permission-review/friction.json` and other machine-specific sources: possible later inputs, not in 0.9.x.
- Non-factory sessions.
- Automatic push or merge of anything.
