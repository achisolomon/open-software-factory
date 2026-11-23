# Software Factory plugin

A team of AI agents for Claude Code that builds software end to end: a Team Lead orchestrates a Triage Classifier, Product Manager, Project Manager, Software Architect, Developer, Test Automation, Exploratory QA, DevOps, FinOps, Security, UX/UI Designer and Performance Engineer, with a shared handoff contract and approval gates.

Install:

```bash
claude plugin marketplace add achisolomon/open-software-factory && claude plugin install software-factory@software-factory-marketplace --scope user
```

This folder is the plugin itself: `agents/`, `skills/`, `hooks/`, `reference/` and `scripts/review-route`. Docs, evals and tests live in the repository root.

- Website: https://opensoftwarefactory.ai/
- Setup, usage and the full team: https://github.com/achisolomon/open-software-factory#readme
- What each hook does and why: https://github.com/achisolomon/open-software-factory/blob/main/docs/TRUST.md
- Release notes: https://github.com/achisolomon/open-software-factory/blob/main/CHANGELOG.md

License: AGPL-3.0-only (see `LICENSE`).
