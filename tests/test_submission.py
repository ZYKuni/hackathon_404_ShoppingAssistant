from __future__ import annotations

import unittest

from starter.agent import Agent as StarterAgent
from submission.agent import Agent as SubmissionAgent


class SubmissionScaffoldTest(unittest.TestCase):
    def test_official_entry_point_exports_current_agent(self) -> None:
        self.assertIs(SubmissionAgent, StarterAgent)


if __name__ == "__main__":
    unittest.main()
