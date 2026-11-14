"""The release files must agree with the plugin: version, agent count, eval count."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"


AGENT_DISPLAY_NAMES = {
    "team-lead": "Team Lead",
    "triage-classifier": "Triage Classifier",
    "product-manager": "Product Manager",
    "project-manager": "Project Manager",
    "software-architect": "Software Architect",
    "software-developer": "Software Developer",
    "test-automation-engineer": "Test Automation Engineer",
    "exploratory-qa-engineer": "Exploratory QA Engineer",
    "devops-engineer": "DevOps Engineer",
    "finops-engineer": "FinOps Engineer",
    "security-engineer": "Security Engineer",
    "ux-ui-designer": "UX/UI Designer",
    "performance-engineer": "Performance Engineer",
    "retro": "Retro",
}


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


class ReleaseFilesTests(unittest.TestCase):
    def test_plugin_version_matches_newest_changelog_heading(self):
        version = json.loads(read("plugin/.claude-plugin/plugin.json"))["version"]
        newest = re.search(r"^## (\d+\.\d+\.\d+)", read("CHANGELOG.md"), re.M).group(1)
        self.assertEqual(version, newest)

    def test_readme_current_version_matches_plugin(self):
        version = json.loads(read("plugin/.claude-plugin/plugin.json"))["version"]
        self.assertIn(f"Current version: {version}.", read("README.md"))

    def test_readme_agent_count_matches_agent_files(self):
        count = len(list((PLUGIN / "agents").glob("*.md")))
        self.assertIn(f"{count} agents, what each one does", read("README.md"))

    def test_readme_eval_count_matches_eval_prompts(self):
        count = len(list((ROOT / "evals").glob("*/prompt.md")))
        self.assertIn(f"{count} behavior evals", read("README.md"))

    def test_readme_test_count_matches_test_functions(self):
        count = sum(
            len(re.findall(r"^\s*def test_", path.read_text(encoding="utf-8"), re.M))
            for path in (ROOT / "tests").glob("*.py")
        )
        self.assertIn(f"{count} offline tests", read("README.md"))

    def test_readme_has_a_retro_row_that_does_not_overclaim_privacy(self):
        row = next(line for line in read("README.md").splitlines() if line.startswith("| **Retro**"))
        self.assertIn("your messages are never quoted", row)
        self.assertNotIn("never leave it", row)

    def test_retro_skill_and_wiring(self):
        skill = read("plugin/skills/retro/SKILL.md")
        self.assertIn("name: retro\n", skill)
        review = skill.split("## `review`", 1)[1].split("## Run a retro now", 1)[0]
        self.assertLess(review.index("`<CHECK> check <entry file>`"), review.index("create branch"))
        self.assertNotIn("python3 -c", skill)
        self.assertIn("json.dumps", review)
        self.assertIn("`withdrawn`", review)
        self.assertIn("`<CHECK> scrub <changed files> --strict`", review)
        self.assertIn("--due-out <STATE>/retros/<today>/<slug>-now/`", skill)
        self.assertIn("must sit inside `<STATE>/retros/`", skill)
        self.assertIn("never a worktree path", skill)
        self.assertIn("reach only `<STATE>/retros/<today>/<slug>-now/work/` and `<STATE>/upstream/`", skill)
        self.assertIn("Run folder `<STATE>/retros/<today>/<slug>-now/`", skill)
        self.assertIn("retro-scan --project <project> [--branch <branch>] [--since <since>] --out", skill)  # #203
        self.assertIn("Add `--branch <branch>` only when the user named a branch", skill)
        self.assertIn("`--since <since>` only when the user gave a start date", skill)
        self.assertIn("`$SOFTWARE_FACTORY_HOME`", read("plugin/skills/session-setup/SKILL.md"))
        lead = read("plugin/agents/team-lead.md")
        self.assertIn("whose file name the brain does not yet name", lead)
        self.assertIn("Apply \"The factory brain\" rules", lead)
        setup = read("plugin/skills/session-setup/SKILL.md")
        self.assertNotIn("always ask", setup)
        self.assertIn("Do not offer auto-fix", setup)
        self.assertNotIn("`auto-fix`", setup)
        self.assertNotIn("auto-fix", read("CHANGELOG.md").split("## ", 2)[1])
        self.assertNotIn("auto-fix", read("README.md"))
        self.assertIn("- **Overlay**: the overlay lines for this role", read("plugin/agents/team-lead.md"))

    def test_readme_team_table_lists_every_agent(self):
        # An agent added without a display name here fails the test on purpose.
        table = read("README.md").split("## Meet the team", 1)[1].split("## Quick start", 1)[0]
        for path in sorted((PLUGIN / "agents").glob("*.md")):
            display_name = AGENT_DISPLAY_NAMES.get(path.stem)
            self.assertIsNotNone(display_name, f"add {path.stem} to AGENT_DISPLAY_NAMES")
            self.assertIn(f"| **{display_name}** |", table, path.stem)

    def test_provenance_records_tester_army_e2e(self):
        provenance = read("docs/provenance.md")
        for needle in ("tester-army/e2e", "2026-11-02", "Apache-2.0"):
            self.assertIn(needle, provenance)

    def test_release_files_contain_no_em_dash(self):
        for name in ("CHANGELOG.md", "README.md", "docs/provenance.md", "docs/index.html"):
            self.assertNotIn("—", read(name), name)


if __name__ == "__main__":
    unittest.main()
