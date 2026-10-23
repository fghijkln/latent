"""Keep CI dependencies immutable without broadening workflow permissions."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WorkflowPinTests(unittest.TestCase):
    def test_all_external_actions_use_full_commit_sha(self):
        references = []
        for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            for line in workflow.read_text().splitlines():
                match = re.search(r"\buses:\s*(\S+)", line)
                if match:
                    reference = match.group(1)
                    references.append(reference)
                    with self.subTest(workflow=workflow.name, reference=reference):
                        self.assertRegex(reference, r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
        self.assertTrue(references, "no workflow action references were checked")


if __name__ == "__main__":
    unittest.main()
