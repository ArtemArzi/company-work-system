"""The public product tree does not depend on private development records."""
from pathlib import Path
import subprocess
import unittest


PRODUCT = Path(__file__).resolve().parents[1]
PRIVATE_PREFIXES = ("docs/development/", "development/", "evidence/", "factory-workspaces/", ".local/")


def private_paths(paths):
    return [name for name in paths if name.startswith(PRIVATE_PREFIXES)]


class ProductBoundary(unittest.TestCase):
    def test_public_index_excludes_private_records(self):
        result = subprocess.run(["git", "ls-files", "-z"], cwd=PRODUCT, capture_output=True, check=True)
        paths = [p.decode("utf-8") for p in result.stdout.split(b"\0") if p]
        self.assertEqual(private_paths(paths), [])

    def test_boundary_detects_known_bad_paths_without_hiding_company_inputs(self):
        bad = ["docs/development/PLAN.md", "factory-workspaces/jobs/private.md", "evidence/evals.json"]
        good = ["template/work/task-id/inputs/input.json", "template/work/task-id/evidence.json", "template/docs/first-company.md"]
        self.assertEqual(private_paths(bad + good), bad)

    def test_public_navigation_has_no_removed_document_links(self):
        for name in ["README.md", "AGENTS.md", "template/standards/hook-authoring.md"]:
            with self.subTest(name=name):
                self.assertNotIn("](docs/development/", (PRODUCT / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
