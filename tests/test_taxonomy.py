import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import taxonomy

def run_main(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = taxonomy.main(argv)
    return code, buf.getvalue()

TABLE = json.loads((ROOT / "otl-relations.json").read_text(encoding="utf-8"))

AUDIT_GRAPH = {
    "nodes": [{"id": "pr:1"}, {"id": "pr:2"}, {"id": "pr:3"}, {"id": "run:1"}],
    "edges": [
        {"source": "pr:2", "relation": "dependsOn", "target": "pr:1"},
        {"source": "pr:3", "relation": "dependsOn", "target": "pr:1"},
        {"source": "pr:1", "relation": "duplicates", "target": "pr:3"},
        {"source": "run:1", "relation": "observes", "target": "pr:1"},
    ],
}

CONCEPTS_GRAPH = {
    "nodes": [{"id": "A"}, {"id": "B"}, {"id": "C"}],
    "edges": [
        {"source": "B", "kind": "narrower", "target": "A"},
        {"source": "C", "kind": "narrower", "target": "A"},
        {"source": "B", "kind": "similarTo", "target": "C"},
        {"source": "C", "kind": "similarTo", "target": "B"},
        {"source": "A", "kind": "hasName", "target": "B"},
    ],
}

CASES_GRAPH = {
    "edges": [
        {"kind": "candidate", "source": "case:1", "target": "concept:x"},
        {"kind": "to_model", "source": "case:1", "target": "elem:y"},
        {"kind": "judgment", "source": "case:1", "target": "judgment:1:0"},
        {"kind": "holds true", "source": "judgment:1:0", "target": "concept:x"},
        {"kind": "holds false", "source": "judgment:1:1", "target": "concept:z"},
    ],
}


class ValidateTests(unittest.TestCase):
    def test_checked_in_table_is_valid(self):
        self.assertEqual(taxonomy.validate_table(TABLE), [])
        self.assertEqual(set(TABLE), {"audit", "concepts", "cases"})

    def test_rejects_bad_shapes(self):
        self.assertTrue(taxonomy.validate_table([]))
        self.assertTrue(taxonomy.validate_table({}))
        self.assertTrue(taxonomy.validate_table({"audit": "dependency"}))
        self.assertTrue(taxonomy.validate_table({"audit": {}}))
        self.assertTrue(taxonomy.validate_table({"audit": {"": "dependency"}}))
        self.assertTrue(taxonomy.validate_table({"audit": {"dependsOn": ""}}))
        self.assertTrue(taxonomy.validate_table({"audit": {"dependsOn": 1}}))

    def test_cli_validate_ok_and_json(self):
        self.assertEqual(run_main(["validate", str(ROOT / "otl-relations.json")])[0], 0)
        self.assertEqual(run_main(["validate", str(ROOT / "otl-relations.json"), "--json"])[0], 0)


class CoverageTests(unittest.TestCase):
    def test_audit_reports_unmapped_observes(self):
        report = taxonomy.coverage(AUDIT_GRAPH, TABLE["audit"])
        self.assertEqual({row["relation"] for row in report["mapped"]}, {"dependsOn", "duplicates"})
        self.assertEqual(report["unmapped"], [{"relation": "observes", "edges": 1}])
        self.assertEqual(report["unused_in_graph"], [])
        self.assertEqual(report["edge_count"], 4)

    def test_concepts_kind_field_and_unused(self):
        report = taxonomy.coverage(CONCEPTS_GRAPH, TABLE["concepts"])
        self.assertEqual(report["unmapped"], [{"relation": "hasName", "edges": 1}])
        narrower = next(row for row in report["mapped"] if row["relation"] == "narrower")
        self.assertEqual(narrower, {"relation": "narrower", "class": "hierarchy", "edges": 2})

    def test_cases_all_mapped(self):
        report = taxonomy.coverage(CASES_GRAPH, TABLE["cases"])
        self.assertEqual(report["unmapped"], [])
        self.assertEqual(report["unused_in_graph"], [])
        self.assertEqual(len(report["mapped"]), 5)

    def test_strict_exit_and_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = Path(tmp) / "graph.json"
            graph.write_text(json.dumps(AUDIT_GRAPH), encoding="utf-8")
            table = ROOT / "otl-relations.json"
            self.assertEqual(run_main(["coverage", str(graph), str(table), "audit"])[0], 0)
            self.assertEqual(run_main(["coverage", str(graph), str(table), "audit", "--strict"])[0], 2)
            self.assertEqual(run_main(["coverage", str(graph), str(table), "audit", "--format", "text"])[0], 0)
            self.assertEqual(run_main(["coverage", str(graph), str(table), "missing"])[0], 1)


if __name__ == "__main__":
    unittest.main()
