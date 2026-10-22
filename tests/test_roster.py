import json
import tempfile
import unittest
from pathlib import Path

from fleet.hub import SourceError, describe
from fleet.sources.roster import Roster


class RosterTest(unittest.TestCase):
    def collect(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pool.json"
            if text is not None:
                path.write_text(text)
            return Roster(str(path)).collect(None)

    def code(self, text):
        with self.assertRaises(SourceError) as caught:
            self.collect(text)
        return describe(caught.exception)

    def test_roots_are_read(self):
        self.assertEqual(self.collect(json.dumps({"wipRoots": ["mcp:a", "mcp:b"]})), {"wipRoots": ["mcp:a", "mcp:b"]})

    def test_a_file_without_roots_is_an_empty_roster(self):
        self.assertEqual(self.collect(json.dumps({"pools": []})), {"wipRoots": []})

    def test_failures_carry_short_codes(self):
        self.assertEqual(self.code(None), "roster-unavailable")
        self.assertEqual(self.code("{not json"), "roster-invalid")
        self.assertEqual(self.code(json.dumps({"wipRoots": "mcp:a"})), "roster-invalid")
        self.assertEqual(self.code(json.dumps(["mcp:a"])), "roster-invalid")


if __name__ == "__main__":
    unittest.main()
