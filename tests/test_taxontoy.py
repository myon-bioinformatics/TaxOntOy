"""CLI tests for taxontoy. Paths are arguments; ONTO_CHECKOUT is the pinned checkout."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "otl-relations.json"
ONTO = os.environ.get("ONTO_CHECKOUT")
_COVER_DIR = Path(tempfile.mkdtemp(prefix="taxontoy-lines-"))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import branchcov

# The suite's own line and branch partition traces this process and CLI children.
# An external coverage.py run sees only the children, because this hook replaces the tracer.
branchcov.install()

import taxontoy


_WRAPPER = """
import importlib.util, runpy, sys
from pathlib import Path
script, dest, helper = sys.argv[1], sys.argv[2], sys.argv[3]
spec = importlib.util.spec_from_file_location("branchcov", helper)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.install()
sys.argv = [script, *sys.argv[4:]]
status = 0
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as exc:
    status = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
Path(dest).write_text(mod.dump(), encoding="utf-8")
raise SystemExit(status)
"""


def run(args, *, cwd=None, env=None):
    dest = _COVER_DIR / f"{len(list(_COVER_DIR.iterdir()))}.txt"
    return subprocess.run(
        [
            sys.executable, "-S", "-c", _WRAPPER,
            str(ROOT / "taxontoy.py"), str(dest), str(ROOT / "tests" / "branchcov.py"), *args,
        ],
        cwd=ROOT if cwd is None else cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def write_json(directory, name, payload):
    path = Path(directory) / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def minimal_table():
    return {
        "schema": taxontoy.SCHEMA_V2,
        "audit": {"dependsOn": "dependency", "duplicates": "overlap"},
        "concepts": {"narrower": "hierarchy", "similarTo": "similarity"},
        "cases": {
            "candidate": "hypothesis",
            "holds true": "affirmation",
            "judgment": "judgment-record",
            "to_model": "unknown-element",
        },
        "direction": {
            "candidate": "forward",
            "dependsOn": "forward",
            "duplicates": "undirected",
            "holds true": "forward",
            "judgment": "forward",
            "narrower": "forward",
            "similarTo": "mutual",
            "to_model": "forward",
        },
        "excluded": {
            "audit": ["blocks", "depends_on", "observes", "overlapsWith"],
            "concepts": ["hasName"],
            "cases": [],
        },
        "properties": {"classes": {}, "relations": {}},
    }


def fixture_graphs():
    return {
        "audit": {"edges": [
            {"source": "a", "relation": "dependsOn", "target": "b"},
            {"source": "a", "relation": "depends_on", "target": "b"},
            {"source": "b", "relation": "blocks", "target": "a"},
            {"source": "a", "relation": "duplicates", "target": "c"},
            {"source": "a", "relation": "overlapsWith", "target": "c"},
            {"source": "x", "relation": "observes", "target": "y"},
        ]},
        "concepts": {"edges": [
            {"source": "parent", "kind": "narrower", "target": "child"},
            {"source": "m", "kind": "similarTo", "target": "n"},
            {"source": "n", "kind": "similarTo", "target": "m"},
            {"source": "name", "kind": "hasName", "target": "m"},
        ]},
        "cases": {"edges": [
            {"kind": "candidate", "source": "case:1", "target": "concept:x"},
            {"kind": "to_model", "source": "case:1", "target": "elem:y"},
            {"kind": "judgment", "source": "case:1", "target": "judgment:1"},
            {"kind": "holds true", "source": "judgment:1", "target": "concept:x"},
        ]},
    }


class CheckedInTableTests(unittest.TestCase):
    def test_table_valid_and_tol_can_read_class_strings(self):
        data = json.loads(TABLE.read_text(encoding="utf-8"))
        self.assertEqual(taxontoy.validate_document(data), [])
        concepts = data["concepts"]
        self.assertEqual(concepts["narrower"], "hierarchy")
        self.assertTrue(all(isinstance(value, str) for value in concepts.values()))
        self.assertEqual(sorted(set(concepts.values())), ["hierarchy", "similarity"])
        self.assertNotIn("holds false", data["cases"])
        self.assertNotIn("depends_on", data["audit"])
        excluded = {item["relation"]: item for item in data["excluded"]["audit"]}
        self.assertEqual(excluded["depends_on"]["kind"], "alias")
        self.assertEqual(excluded["depends_on"]["alias_of"], "dependsOn")
        self.assertEqual(excluded["overlapsWith"]["kind"], "alias")
        self.assertEqual(excluded["overlapsWith"]["alias_of"], "duplicates")
        self.assertEqual(excluded["belongsTo"]["kind"], "out_of_scope")
        self.assertNotIn("alias_of", excluded["belongsTo"])
        self.assertIn("negation", taxontoy.CLASS_DIRECTION)
        self.assertEqual(data["repos"]["subtree"], "hierarchy")
        self.assertEqual(data["repos"]["vendor"], "dependency")
        self.assertEqual(data["schema"], "taxontoy-relations/3")
        self.assertEqual(data["excluded"]["repos"], [])
        self.assertEqual(
            data["properties"]["relations"]["belongsTo"]["domain"]["types"],
            ["ActionsRun", "Branch", "Commit", "File", "PullRequest"],
        )
        self.assertEqual(data["properties"]["relations"]["belongsTo"]["range"]["types"], ["Repository"])

    def test_repos_profile_is_optional_on_a_version_2_table(self):
        data = json.loads(TABLE.read_text(encoding="utf-8"))
        del data["repos"]
        del data["excluded"]["repos"]
        for name in ("consumes", "subtree", "vendor", "workflow-pin"):
            del data["direction"][name]
        for name in ("subtree", "vendor"):
            del data["properties"]["relations"][name]
        self.assertEqual(taxontoy.validate_document(data), [])
        present = minimal_table()
        present["excluded"]["repos"] = []
        self.assertIn("excluded: unknown profile repos", taxontoy.validate_document(present))
        missing = minimal_table()
        missing["repos"] = {"subtree": "hierarchy"}
        missing["direction"]["subtree"] = "forward"
        self.assertIn("excluded.repos: must be an array", taxontoy.validate_document(missing))

    def test_schema_lists_the_same_classes(self):
        schema = json.loads((ROOT / "otl-relations.schema.json").read_text(encoding="utf-8"))
        enum = set(schema["$defs"]["profile"]["additionalProperties"]["enum"])
        self.assertEqual(enum, set(taxontoy.CLASS_DIRECTION))

    def test_package_metadata_matches_the_module(self):
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]
        self.assertEqual(project["version"], taxontoy.__version__)
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["scripts"]["taxontoy"], "taxontoy:main")

    def test_entry_point_does_not_search_or_modify_import_path(self):
        source = (ROOT / "taxontoy.py").read_text(encoding="utf-8")
        self.assertNotIn("sys.path", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("subprocess", source)
        self.assertFalse((ROOT / "taxonomy.py").exists())
        before = list(sys.path)
        self.assertEqual(taxontoy.main(["validate", str(TABLE)]), 0)
        self.assertEqual(sys.path, before)

    def test_cli_validate_json_is_deterministic_and_records_provenance(self):
        first = run(["validate", "otl-relations.json", "--format", "json"])
        second = run(["validate", "otl-relations.json", "--json"])
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout, second.stdout)
        payload = json.loads(first.stdout)
        self.assertEqual(
            first.stdout,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        self.assertEqual(payload["tool"], "taxontoy")
        self.assertEqual(payload["version"], taxontoy.__version__)
        by_key = {(row["scope"], row["name"], row["property"]): row for row in payload["properties"]}
        self.assertEqual(by_key[("relation", "dependsOn", "inverse_of")]["relation"], "blocks")
        self.assertEqual(by_key[("relation", "narrower", "disjoint_with")]["relation"], "similarTo")
        self.assertNotIn("relation", by_key[("class", "similarity", "symmetric")])
        text = run(["validate", "otl-relations.json", "--format", "text"])
        self.assertEqual(text.returncode, 0, text.stderr + text.stdout)
        self.assertIn("property relation dependsOn inverse_of blocks since 2026-11-14", text.stdout)
        self.assertIn("property relation narrower disjoint_with similarTo since 2026-11-15", text.stdout)
        self.assertIn("property class similarity symmetric since 2026-11-15", text.stdout)
        self.assertEqual(payload["inputs"], [{
            "path": "otl-relations.json",
            "sha256": hashlib.sha256(TABLE.read_bytes()).hexdigest(),
        }])

    def test_format_conflict_is_rejected(self):
        result = run(["validate", str(TABLE), "--json", "--format", "text"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("disagree", result.stderr)

    def test_swapping_a_class_makes_validate_fail(self):
        data = json.loads(TABLE.read_text(encoding="utf-8"))
        data["concepts"]["narrower"] = "similarity"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(tmp, "table.json", data)
            result = run(["validate", str(path), "--json"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires mutual", result.stdout)

    def test_duplicate_key_is_rejected(self):
        text = '{"schema":"taxontoy-relations/1","audit":{"dependsOn":"dependency","dependsOn":"overlap"}}'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "table.json"
            path.write_text(text, encoding="utf-8")
            result = run(["validate", str(path)])
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate key", result.stderr)


class CoverageTests(unittest.TestCase):
    def setUp(self):
        if not ONTO:
            self.fail("set ONTO_CHECKOUT to the pinned ONTO checkout")
        self.onto = str(Path(ONTO).resolve())

    def _cover(self, table, graphs, *extra):
        directory = Path(table).parent
        args = ["coverage", str(table), "--onto", self.onto, "--format", "json", *extra]
        for wave, payload in graphs.items():
            path = write_json(directory, f"{wave}.json", payload)
            args.extend(["--graph", f"{wave}={path}"])
        return run(args)

    def test_closed_partition_reports_alias_candidates_without_folding(self):
        with tempfile.TemporaryDirectory() as tmp:
            table = write_json(tmp, "table.json", minimal_table())
            result = self._cover(table, fixture_graphs())
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        audit = payload["waves"]["audit"]
        part = audit["partition"]
        self.assertEqual(part["classified"] + part["excluded"] + part["unclassified"], part["total"])
        self.assertEqual(part["unclassified"], 0)
        self.assertEqual(part["stale"], 0)
        self.assertTrue(part["holds"])
        self.assertEqual(audit["relations"]["dependsOn"]["edges"], 1)
        self.assertNotIn("depends_on", audit["relations"])
        self.assertEqual(audit["class_edges"]["dependency"], 1)
        candidates = audit["alias_candidates"]
        self.assertIn(
            {"detection": "separator", "folded": False, "relations": ["dependsOn", "depends_on"]},
            candidates,
        )
        self.assertIn(
            {"detection": "same-pairs", "folded": False, "relations": ["duplicates", "overlapsWith"]},
            candidates,
        )
        self.assertIn(
            {"detection": "inverse-pair", "folded": False, "relations": ["blocks", "dependsOn"]},
            candidates,
        )
        self.assertTrue(all(item["folded"] is False for item in candidates))
        self.assertEqual(payload["onto"]["sha256"], hashlib.sha256((Path(self.onto) / "knowledgegraph.py").read_bytes()).hexdigest())
        again = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self.assertEqual(result.stdout, again)

    def test_removing_one_entry_makes_coverage_fail(self):
        table = minimal_table()
        del table["concepts"]["narrower"]
        del table["direction"]["narrower"]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(tmp, "table.json", table)
            result = self._cover(path, fixture_graphs())
            strict = self._cover(path, fixture_graphs(), "--strict")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unclassified concepts: narrower", result.stdout)
        self.assertEqual(strict.returncode, 2)

    def test_class_swap_onto_mutual_fails_when_the_graph_is_one_way(self):
        table = minimal_table()
        table["concepts"]["narrower"] = "similarity"
        table["direction"]["narrower"] = "mutual"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(tmp, "table.json", table)
            result = self._cover(path, fixture_graphs())
        self.assertEqual(result.returncode, 1)
        self.assertIn("mutual relation is not symmetric", result.stdout)

    def test_stale_exclusion_fails(self):
        table = minimal_table()
        table["excluded"]["audit"] = sorted([*table["excluded"]["audit"], "ghost"])
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(tmp, "table.json", table)
            result = self._cover(path, fixture_graphs())
        self.assertEqual(result.returncode, 1)
        self.assertIn("stale audit: ghost", result.stdout)

    def test_output_path_is_the_only_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            table = write_json(tmp, "table.json", minimal_table())
            graphs = fixture_graphs()
            args = ["coverage", str(table), "--onto", self.onto, "--json"]
            for wave, payload in graphs.items():
                path = write_json(tmp, f"{wave}.json", payload)
                args.extend(["--graph", f"{wave}={path}"])
            before = set(tmp.iterdir())
            quiet = run(args, cwd=tmp)
            self.assertEqual(quiet.returncode, 0, quiet.stderr + quiet.stdout)
            self.assertEqual(set(tmp.iterdir()), before)
            out = tmp / "report.json"
            written = run([*args, "--output", str(out)], cwd=tmp)
            self.assertEqual(written.returncode, 0)
            self.assertEqual(written.stdout, "")
            self.assertTrue(out.is_file())
            self.assertEqual(set(tmp.iterdir()), before | {out})


class CommittedReportTests(unittest.TestCase):
    def test_committed_coverage_is_closed(self):
        path = ROOT / "coverage.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["version"], taxontoy.__version__)
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        counts = {
            "audit": {"dependency": 13, "overlap": 25},
            "concepts": {"hierarchy": 29, "similarity": 62},
            "cases": {"affirmation": 3, "hypothesis": 3, "judgment-record": 3, "unknown-element": 1},
        }
        for wave, expected in counts.items():
            body = payload["waves"][wave]
            part = body["partition"]
            self.assertEqual(part["classified"] + part["excluded"] + part["unclassified"], part["total"])
            self.assertEqual(part["unclassified"], 0)
            self.assertEqual(part["stale"], 0)
            self.assertEqual(body["class_edges"], expected)
            self.assertTrue(all(item["folded"] is False for item in body["alias_candidates"]))
            weight = body["edge_weight"]
            ratio = weight["classified_ratio"]
            self.assertEqual(ratio["numerator"], weight["classified"])
            self.assertEqual(ratio["denominator"], weight["total"])
            self.assertEqual(
                weight["classified"] + weight["excluded"] + weight["unclassified"],
                weight["total"],
            )
        self.assertEqual(payload["waves"]["audit"]["edge_weight"]["classified"], 38)
        self.assertEqual(payload["waves"]["audit"]["edge_weight"]["excluded"], 2304)
        self.assertEqual(payload["waves"]["audit"]["edge_weight"]["total"], 2342)
        self.assertEqual(payload["waves"]["concepts"]["edge_weight"]["classified"], 91)
        self.assertEqual(payload["waves"]["concepts"]["edge_weight"]["total"], 826)
        self.assertEqual(payload["waves"]["cases"]["edge_weight"], {
            "classified": 10,
            "classified_ratio": {"denominator": 10, "numerator": 10},
            "excluded": 0,
            "total": 10,
            "unclassified": 0,
        })
        belongs = payload["waves"]["audit"]["types"]["relations"]["belongsTo"]
        self.assertEqual(belongs["declared"]["range"], ["Repository"])
        self.assertEqual(
            [(row["source"], row["edges"]) for row in belongs["actual"]],
            [("ActionsRun", 93), ("Branch", 9), ("Commit", 32), ("File", 542), ("PullRequest", 15)],
        )
        self.assertEqual(belongs["gap"], {"actual_only": [], "declared_only": []})
        candidates = payload["waves"]["audit"]["alias_candidates"]
        self.assertIn(
            {"detection": "separator", "folded": False, "relations": ["dependsOn", "depends_on"]},
            candidates,
        )
        self.assertIn(
            {"detection": "same-pairs", "folded": False, "relations": ["duplicates", "overlapsWith"]},
            candidates,
        )
        if ONTO:
            digest = hashlib.sha256((Path(ONTO).resolve() / "knowledgegraph.py").read_bytes()).hexdigest()
            self.assertEqual(payload["onto"]["sha256"], digest)

    def test_committed_candidates_match_declared_properties(self):
        path = ROOT / "candidates.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["version"], taxontoy.__version__)
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        audit = payload["waves"]["audit"]["candidates"]
        self.assertIn({
            "declared": True,
            "detection": "separator",
            "folded": False,
            "relations": ["dependsOn", "depends_on"],
        }, audit)
        self.assertIn({
            "declared": True,
            "detection": "same-pairs",
            "folded": False,
            "relations": ["dependsOn", "depends_on"],
        }, audit)
        self.assertIn({
            "declared": True,
            "detection": "same-pairs",
            "folded": False,
            "relations": ["duplicates", "overlapsWith"],
        }, audit)
        self.assertIn({
            "declared": True,
            "detection": "inverse-pair",
            "folded": False,
            "relations": ["blocks", "dependsOn"],
        }, audit)
        self.assertEqual(
            audit,
            sorted(audit, key=lambda item: (item["detection"], item["relations"])),
        )
        polysemy = next(item for item in audit if item["detection"] == "polysemy")
        self.assertEqual(polysemy["relations"], ["belongsTo"])
        self.assertEqual(len(polysemy["patterns"]), 5)
        concepts = payload["waves"]["concepts"]["candidates"]
        self.assertIn({
            "declared": True,
            "detection": "symmetric",
            "folded": False,
            "relations": ["similarTo"],
        }, concepts)
        self.assertEqual(payload["waves"]["cases"]["notes"], ["polysemy: skipped; nodes have no type"])
        self.assertTrue(all(not item["folded"] for wave in payload["waves"].values() for item in wave["candidates"]))



class ValidateErrorTests(unittest.TestCase):
    def test_validate_document_error_branches(self):
        cases = []

        cases.append((["not a table"], ["table must be a JSON object"]))

        missing = {key: value for key, value in minimal_table().items() if key != "excluded"}
        cases.append((missing, ["missing key: excluded"]))

        extra = minimal_table()
        extra["unexpected"] = {}
        cases.append((extra, ["unknown key: unexpected"]))

        schema = minimal_table()
        schema["schema"] = "taxontoy-relations/0"
        cases.append((schema, [f"schema: expected {taxontoy.SCHEMA_V2} or {taxontoy.SCHEMA_ID}"]))

        not_object = minimal_table()
        not_object["audit"] = []
        cases.append((not_object, ["audit: profile must be an object"]))

        empty = minimal_table()
        empty["audit"] = {}
        del empty["direction"]["dependsOn"]
        del empty["direction"]["duplicates"]
        cases.append((empty, ["audit: profile has no relations"]))

        bad_name = minimal_table()
        bad_name["audit"] = {"": "dependency", "duplicates": "overlap"}
        del bad_name["direction"]["dependsOn"]
        cases.append((bad_name, ["audit: relation names must be non-empty strings"]))

        padded = minimal_table()
        padded["audit"] = {" dependsOn": "dependency", "duplicates": "overlap"}
        del padded["direction"]["dependsOn"]
        cases.append((padded, ["audit: relation names must be non-empty strings"]))

        doubled = minimal_table()
        doubled["concepts"]["dependsOn"] = "hierarchy"
        doubled["direction"]["dependsOn"]  # already present from audit
        cases.append((doubled, ["relation classified in two profiles: dependsOn"]))

        unknown_class = minimal_table()
        unknown_class["audit"]["dependsOn"] = "not-a-class"
        cases.append((unknown_class, ["audit.dependsOn: unknown class 'not-a-class'"]))

        direction_not_obj = minimal_table()
        direction_not_obj["direction"] = []
        cases.append((direction_not_obj, ["direction: must be an object"]))

        direction_bad_name = minimal_table()
        direction_bad_name["direction"][""] = "forward"
        cases.append((direction_bad_name, ["direction: names must be non-empty strings"]))

        unknown_direction = minimal_table()
        unknown_direction["direction"]["dependsOn"] = "sideways"
        cases.append((unknown_direction, ["direction.dependsOn: unknown direction 'sideways'"]))

        missing_direction = minimal_table()
        del missing_direction["direction"]["dependsOn"]
        cases.append((missing_direction, ["direction: missing dependsOn"]))

        extra_direction = minimal_table()
        extra_direction["direction"]["ghost"] = "forward"
        cases.append((extra_direction, ["direction: unknown relation ghost"]))

        excluded_not_obj = minimal_table()
        excluded_not_obj["excluded"] = []
        cases.append((excluded_not_obj, ["excluded: must be an object"]))

        excluded_unknown = minimal_table()
        excluded_unknown["excluded"]["other"] = []
        cases.append((excluded_unknown, ["excluded: unknown profile other"]))

        excluded_not_list = minimal_table()
        excluded_not_list["excluded"]["audit"] = "blocks"
        cases.append((excluded_not_list, ["excluded.audit: must be an array"]))

        excluded_unsorted = minimal_table()
        excluded_unsorted["excluded"]["audit"] = ["observes", "blocks"]
        cases.append((excluded_unsorted, ["excluded.audit: must be sorted and unique"]))

        excluded_bad_name = minimal_table()
        excluded_bad_name["excluded"]["audit"] = ["", "blocks"]
        cases.append((excluded_bad_name, ["excluded.audit: names must be non-empty strings"]))

        both = minimal_table()
        both["excluded"]["audit"] = sorted([*both["excluded"]["audit"], "dependsOn"])
        cases.append((both, ["audit: dependsOn is both classified and excluded"]))

        for data, expected in cases:
            errors = taxontoy.validate_document(data)
            for message in expected:
                self.assertIn(message, errors, msg=data)

    def test_class_direction_mismatch_is_reported(self):
        data = minimal_table()
        data["concepts"]["narrower"] = "similarity"
        data["direction"]["narrower"] = "forward"
        errors = taxontoy.validate_document(data)
        self.assertIn("concepts.narrower: class similarity requires mutual, found forward", errors)

    def test_cli_validate_rejects_a_json_array(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "table.json"
            path.write_text("[]\n", encoding="utf-8")
            result = run(["validate", str(path)])
        self.assertEqual(result.returncode, 1)
        self.assertIn("table must be a JSON object", result.stdout)

    def test_cli_validate_text_lists_errors(self):
        data = minimal_table()
        del data["excluded"]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(tmp, "table.json", data)
            result = run(["validate", str(path), "--format", "text"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("error missing key: excluded", result.stdout)
        self.assertIn("ok no", result.stdout)

    def test_cli_rejects_missing_or_invalid_files(self):
        missing = run(["validate", "/no/such/table.json"])
        self.assertEqual(missing.returncode, 1)
        self.assertIn("taxontoy:", missing.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            bad_json = Path(tmp) / "bad.json"
            bad_json.write_text("{", encoding="utf-8")
            decoded = run(["validate", str(bad_json)])
            self.assertEqual(decoded.returncode, 1)
            self.assertIn("taxontoy:", decoded.stderr)

            binary = Path(tmp) / "binary.json"
            binary.write_bytes(b"\xff\xfe")
            unicode_error = run(["validate", str(binary)])
            self.assertEqual(unicode_error.returncode, 1)
            self.assertIn("taxontoy:", unicode_error.stderr)


class CoveragePathTests(unittest.TestCase):
    def setUp(self):
        if not ONTO:
            self.fail("set ONTO_CHECKOUT to the pinned ONTO checkout")
        self.onto = str(Path(ONTO).resolve())

    def _write_graphs(self, directory, graphs):
        args = []
        for wave, payload in graphs.items():
            path = write_json(directory, f"{wave}.json", payload)
            args.extend(["--graph", f"{wave}={path}"])
        return args

    def test_non_string_endpoints_are_malformed_not_a_crash(self):
        graphs = fixture_graphs()
        graphs["audit"]["edges"].extend([
            ["not", "an", "object"],
            {"source": "a", "target": "b"},
            {"relation": "", "source": "a", "target": "b"},
            {"relation": "dependsOn", "target": "b"},
            {"kind": "dependsOn", "source": ["a"], "target": "b"},
            {"relation": "dependsOn", "source": "a", "target": 1},
            {"relation": "  padded  ", "source": "a", "target": "b"},
        ])
        with tempfile.TemporaryDirectory() as tmp:
            table = write_json(tmp, "table.json", minimal_table())
            args = ["coverage", str(table), "--onto", self.onto, "--format", "json", *self._write_graphs(tmp, graphs)]
            result = run(args)
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertNotIn("TypeError", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("malformed audit: 7", payload["errors"])

    def test_coverage_text_output_lists_waves_and_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            table = write_json(tmp, "table.json", minimal_table())
            args = ["coverage", str(table), "--onto", self.onto, "--format", "text", *self._write_graphs(tmp, fixture_graphs())]
            result = run(args)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("command coverage", result.stdout)
        self.assertIn("onto ", result.stdout)
        self.assertIn("wave audit", result.stdout)
        self.assertIn("partition total ", result.stdout)
        self.assertIn("class dependency edges 1", result.stdout)
        self.assertIn("relation dependsOn class dependency", result.stdout)
        self.assertIn("excluded depends_on edges 1", result.stdout)
        self.assertIn("candidate separator dependsOn depends_on", result.stdout)

    def test_coverage_text_lists_errors_stale_and_unclassified(self):
        table = minimal_table()
        table["excluded"]["audit"] = sorted([*table["excluded"]["audit"], "ghost"])
        graphs = fixture_graphs()
        graphs["concepts"]["edges"].append({"kind": "novel", "source": "p", "target": "q"})
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(tmp, "table.json", table)
            args = ["coverage", str(path), "--onto", self.onto, "--format", "text", *self._write_graphs(tmp, graphs)]
            result = run(args)
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertIn("error stale audit: ghost", result.stdout)
        self.assertIn("error unclassified concepts: novel", result.stdout)
        self.assertIn("stale ghost", result.stdout)
        self.assertIn("unclassified novel edges 1", result.stdout)

    def test_paths_come_from_environment_variables(self):
        with tempfile.TemporaryDirectory() as tmp:
            table = write_json(tmp, "table.json", minimal_table())
            graph_args = self._write_graphs(tmp, fixture_graphs())
            env = os.environ.copy()
            env["TAXONTOY_TABLE"] = str(table)
            env["ONTO_CHECKOUT"] = self.onto
            result = run(["coverage", "--format", "json", *graph_args], env=env)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["onto"]["path"].endswith("knowledgegraph.py"))

    def test_missing_environment_paths_are_errors(self):
        env = os.environ.copy()
        env.pop("TAXONTOY_TABLE", None)
        env.pop("ONTO_CHECKOUT", None)
        no_table = run(["validate", "--json"], env=env)
        self.assertEqual(no_table.returncode, 1)
        self.assertIn("pass the table path or set TAXONTOY_TABLE", no_table.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            table = write_json(tmp, "table.json", minimal_table())
            graph_args = self._write_graphs(tmp, fixture_graphs())
            no_onto = run(["coverage", str(table), "--format", "json", *graph_args], env=env)
        self.assertEqual(no_onto.returncode, 1)
        self.assertIn("pass --onto or set ONTO_CHECKOUT", no_onto.stderr)

    def test_whitespace_only_environment_paths_are_errors(self):
        env = os.environ.copy()
        env["TAXONTOY_TABLE"] = "  \t  "
        env["ONTO_CHECKOUT"] = self.onto
        no_table = run(["validate", "--json"], env=env)
        self.assertEqual(no_table.returncode, 1)
        self.assertIn("pass the table path or set TAXONTOY_TABLE", no_table.stderr)

        env = os.environ.copy()
        env.pop("TAXONTOY_TABLE", None)
        env["ONTO_CHECKOUT"] = "  \t  "
        with tempfile.TemporaryDirectory() as tmp:
            table = write_json(tmp, "table.json", minimal_table())
            graph_args = self._write_graphs(tmp, fixture_graphs())
            no_onto = run(["coverage", str(table), "--format", "json", *graph_args], env=env)
        self.assertEqual(no_onto.returncode, 1)
        self.assertIn("pass --onto or set ONTO_CHECKOUT", no_onto.stderr)

    def test_padded_environment_paths_are_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            table = write_json(tmp, "table.json", minimal_table())
            graph_args = self._write_graphs(tmp, fixture_graphs())
            env = os.environ.copy()
            env["TAXONTOY_TABLE"] = f"  {table}  "
            env["ONTO_CHECKOUT"] = f"  {self.onto}  "
            result = run(["coverage", "--format", "json", *graph_args], env=env)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["onto"]["path"].endswith("knowledgegraph.py"))

    def test_coverage_rejects_a_broken_table(self):
        data = minimal_table()
        del data["excluded"]
        with tempfile.TemporaryDirectory() as tmp:
            table = write_json(tmp, "table.json", data)
            args = ["coverage", str(table), "--onto", self.onto, "--json", *self._write_graphs(tmp, fixture_graphs())]
            result = run(args)
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "coverage")
        self.assertFalse(payload["ok"])
        self.assertIn("missing key: excluded", payload["errors"])
        self.assertNotIn("waves", payload)

    def test_parse_graphs_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            table = write_json(tmp, "table.json", minimal_table())
            graphs = fixture_graphs()
            written = {}
            for wave, payload in graphs.items():
                written[wave] = write_json(tmp, f"{wave}.json", payload)

            def cover(*graph_args):
                return run(["coverage", str(table), "--onto", self.onto, "--json", *graph_args])

            bad_form = cover("--graph", "no-equals")
            self.assertEqual(bad_form.returncode, 1)
            self.assertIn("use --graph audit|concepts|cases=PATH", bad_form.stderr)

            empty_path = cover("--graph", "audit=")
            self.assertEqual(empty_path.returncode, 1)
            self.assertIn("use --graph audit|concepts|cases=PATH", empty_path.stderr)

            duplicate = cover(
                "--graph", f"audit={written['audit']}",
                "--graph", f"concepts={written['audit']}",
                "--graph", f"cases={written['cases']}",
            )
            self.assertEqual(duplicate.returncode, 1)
            self.assertIn("duplicate graph path", duplicate.stderr)

            bad_graph = tmp / "not-graph.json"
            bad_graph.write_text('{"nodes": []}\n', encoding="utf-8")
            not_edges = cover(
                "--graph", f"audit={bad_graph}",
                "--graph", f"concepts={written['concepts']}",
                "--graph", f"cases={written['cases']}",
            )
            self.assertEqual(not_edges.returncode, 1)
            self.assertIn("graph JSON must be an object with an edges array", not_edges.stderr)

            missing = cover(
                "--graph", f"audit={written['audit']}",
                "--graph", f"concepts={written['concepts']}",
            )
            self.assertEqual(missing.returncode, 1)
            self.assertIn("missing graph for cases", missing.stderr)

    def test_forward_symmetric_edges_fail(self):
        graphs = fixture_graphs()
        graphs["audit"]["edges"].append({"source": "b", "relation": "dependsOn", "target": "a"})
        with tempfile.TemporaryDirectory() as tmp:
            table = write_json(tmp, "table.json", minimal_table())
            result = run(["coverage", str(table), "--onto", self.onto, "--json", *self._write_graphs(tmp, graphs)])
        self.assertEqual(result.returncode, 1)
        self.assertIn("direction audit.dependsOn: forward relation is symmetric", result.stdout)

    def test_knowledgegraph_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            table = write_json(tmp, "table.json", minimal_table())
            graph_args = self._write_graphs(tmp, fixture_graphs())
            missing = run(["coverage", str(table), "--onto", str(tmp), "--json", *graph_args])
            self.assertEqual(missing.returncode, 1)
            self.assertIn("knowledgegraph.py is not in", missing.stderr)

            (tmp / "knowledgegraph.py").write_text("def other():\n    return None\n", encoding="utf-8")
            incomplete = run(["coverage", str(table), "--onto", str(tmp), "--json", *graph_args])
            self.assertEqual(incomplete.returncode, 1)
            self.assertIn("has no relation_cycles", incomplete.stderr)

    def test_load_knowledgegraph_rejects_a_spec_without_a_loader(self):
        import importlib.util
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "knowledgegraph.py").write_text("# placeholder\n", encoding="utf-8")
            with mock.patch.object(importlib.util, "spec_from_file_location", return_value=None):
                with self.assertRaisesRegex(ValueError, "cannot load"):
                    taxontoy._load_knowledgegraph(directory)
            spec = mock.Mock()
            spec.loader = None
            with mock.patch.object(importlib.util, "spec_from_file_location", return_value=spec):
                with self.assertRaisesRegex(ValueError, "cannot load"):
                    taxontoy._load_knowledgegraph(directory)

    def test_alias_candidates_include_case_and_skip_empty_pair_sets(self):
        self.assertEqual(taxontoy._separator_key("_foo__bar-"), ("foo", "bar"))
        self.assertEqual(taxontoy._separator_key(" "), ())
        found = taxontoy._alias_candidates({
            "DependsOn": [{"source": "a", "target": "b"}],
            "dependsOn": [{"source": "a", "target": "b"}],
            "empty": [],
        })
        self.assertIn(
            {"detection": "case", "folded": False, "relations": ["DependsOn", "dependsOn"]},
            found,
        )
        self.assertIn(
            {"detection": "same-pairs", "folded": False, "relations": ["DependsOn", "dependsOn"]},
            found,
        )

    def test_remaining_conditional_arcs(self):
        from unittest import mock

        class Handle:
            def __init__(self, suppress):
                self.suppress = suppress

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return self.suppress

            def read(self, _size):
                raise OSError("boom")

        with mock.patch.object(taxontoy.Path, "open", return_value=Handle(True)):
            self.assertEqual(len(taxontoy._sha256("ignored")), 64)
        with mock.patch.object(taxontoy.Path, "open", return_value=Handle(False)):
            with self.assertRaises(OSError):
                taxontoy._sha256("ignored")
        class BoomDate:
            @staticmethod
            def fromisoformat(_value):
                raise TypeError("boom")

        with mock.patch.object(taxontoy.datetime, "date", BoomDate):
            with self.assertRaises(TypeError):
                taxontoy._iso_date("2026-11-15")

        def evidence(body):
            data = minimal_table()
            data["properties"]["classes"]["hierarchy"] = {"transitive": body}
            self.assertTrue(taxontoy.validate_document(data))

        evidence({"since": "", "reason": "because"})
        evidence({"since": 1, "reason": "because"})
        evidence({"since": "  ", "reason": "because"})
        evidence({"since": "2026-11-15", "reason": "   "})

        taxontoy._alias_candidates({
            "aaa": [],
            "zzz": [{"source": "a", "target": "b"}],
        })
        self.assertIsNone(taxontoy._class_of({"audit": [], "concepts": {}, "cases": {}}, "dependsOn"))
        sparse = {
            "audit": {"dependsOn": "dependency"},
            "concepts": {},
            "cases": {},
            "properties": {"classes": [], "relations": []},
        }
        self.assertFalse(taxontoy._flagged({"properties": []}, "dependsOn", "symmetric"))
        self.assertFalse(taxontoy._flagged(sparse, "dependsOn", "symmetric"))
        declared = {
            "audit": {"dependsOn": "dependency"},
            "concepts": {},
            "cases": {},
            "properties": {"classes": {}, "relations": {"dependsOn": {"symmetric": _evidence()}}},
        }
        self.assertTrue(taxontoy._flagged(declared, "dependsOn", "symmetric"))
        self.assertIsNone(taxontoy._inverse_partner({"properties": []}, "dependsOn"))
        self.assertIsNone(taxontoy._inverse_partner(sparse, "dependsOn"))
        blank_inverse = {
            "properties": {"relations": {"dependsOn": {"inverse_of": {"relation": "  "}}}},
        }
        self.assertIsNone(taxontoy._inverse_partner(blank_inverse, "dependsOn"))
        taxontoy.property_candidates(minimal_table(), {
            "empty": [],
            "loop": [{"source": "a", "target": "a"}],
        }, {})
        self.assertFalse(taxontoy._forward_conflict([]))
        self.assertFalse(taxontoy._forward_conflict([{"source": "a", "target": "a"}]))

        table = minimal_table()
        table["audit"]["ghost"] = "dependency"
        table["direction"]["ghost"] = "forward"
        table["excluded"]["audit"] = sorted([*table["excluded"]["audit"], "ghost"])
        knowledgegraph = taxontoy._load_knowledgegraph(self.onto)
        graphs = {wave: payload["edges"] for wave, payload in fixture_graphs().items()}
        waves, _errors = taxontoy.cover_waves(table, graphs, knowledgegraph)
        self.assertFalse(waves["audit"]["partition"]["holds"])
        with mock.patch.object(taxontoy, "_relation_name", side_effect=TypeError("boom")):
            with self.assertRaises(TypeError):
                taxontoy.cover_waves(minimal_table(), graphs, knowledgegraph)

        with self.assertRaisesRegex(ValueError, "use --graph"):
            taxontoy._parse_graphs(["nope=somewhere"])
        with tempfile.TemporaryDirectory() as tmp:
            array = Path(tmp) / "array.json"
            array.write_text("[]\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "edges array"):
                taxontoy._parse_graphs([f"audit={array}"])
            table_path = write_json(tmp, "table.json", minimal_table())
            graph_args = self._write_graphs(tmp, fixture_graphs())
            with mock.patch.object(taxontoy, "_relation_name", side_effect=TypeError("boom")):
                with self.assertRaises(TypeError):
                    taxontoy.main(["candidates", str(table_path), "--json", *graph_args])

        # 3.13+ specializes any/all. The other side is the plain call.
        real_any, real_all = any, all
        taxontoy.any = lambda items: real_any(items)
        taxontoy.all = lambda items: real_all(items)
        try:
            taxontoy.property_candidates(minimal_table(), {
                "loop": [{"source": "a", "target": "a"}],
            }, {})
            self.assertTrue(taxontoy._symmetric([
                {"source": "a", "target": "b"},
                {"source": "b", "target": "a"},
            ]))
            self.assertTrue(taxontoy._forward_conflict([
                {"source": "a", "target": "b"},
                {"source": "b", "target": "a"},
            ]))
            taxontoy.cover_waves(minimal_table(), graphs, knowledgegraph)
        finally:
            del taxontoy.any
            del taxontoy.all

    def test_holds_is_false_when_classified_and_excluded_overlap(self):
        table = minimal_table()
        table["excluded"]["audit"] = sorted([*table["excluded"]["audit"], "dependsOn"])
        knowledgegraph = taxontoy._load_knowledgegraph(self.onto)
        graphs = {wave: payload["edges"] for wave, payload in fixture_graphs().items()}
        waves, errors = taxontoy.cover_waves(table, graphs, knowledgegraph)
        self.assertFalse(waves["audit"]["partition"]["holds"])
        self.assertTrue(any("graph and table name sets do not close" in item for item in errors))


def _evidence(since="2026-11-14", reason="recorded"):
    return {"since": since, "reason": reason}


class PropertyValidationTests(unittest.TestCase):
    def test_each_property_error_is_reported(self):
        samples = []

        unknown = minimal_table()
        unknown["properties"]["relations"]["dependsOn"] = {"reflexive": _evidence()}
        samples.append((unknown, "properties.relations.dependsOn: unknown property reflexive"))

        missing_partner = minimal_table()
        missing_partner["properties"]["relations"]["dependsOn"] = {
            "inverse_of": {"relation": "ghost", **_evidence()}
        }
        samples.append((missing_partner, "properties.relations.dependsOn.inverse_of: unknown relation ghost"))

        one_way = minimal_table()
        one_way["properties"]["relations"]["dependsOn"] = {
            "inverse_of": {"relation": "blocks", **_evidence()}
        }
        samples.append((one_way, "inverse pairs not mutually declared: blocks dependsOn"))

        both_flags = minimal_table()
        both_flags["properties"]["relations"]["dependsOn"] = {
            "symmetric": _evidence(),
            "acyclic": _evidence(),
        }
        samples.append((both_flags, "relation dependsOn: symmetric and acyclic contradict"))

        split = minimal_table()
        split["properties"]["classes"]["dependency"] = {"symmetric": _evidence()}
        split["properties"]["relations"]["dependsOn"] = {"acyclic": _evidence()}
        samples.append((split, "dependsOn: symmetric and acyclic contradict"))

        itself = minimal_table()
        itself["properties"]["relations"]["dependsOn"] = {
            "disjoint_with": [{"relation": "dependsOn", **_evidence()}]
        }
        samples.append((itself, "properties.relations.dependsOn.disjoint_with: disjoint with itself"))

        no_since = minimal_table()
        no_since["properties"]["classes"]["hierarchy"] = {"transitive": {"reason": "because"}}
        samples.append((no_since, "properties.classes.hierarchy.transitive: missing since"))

        no_reason = minimal_table()
        no_reason["properties"]["classes"]["hierarchy"] = {"transitive": {"since": "2026-11-15"}}
        samples.append((no_reason, "properties.classes.hierarchy.transitive: missing reason"))

        bad_date = minimal_table()
        bad_date["properties"]["classes"]["hierarchy"] = {"transitive": _evidence(since="2026-02-31")}
        samples.append((bad_date, "properties.classes.hierarchy.transitive: invalid since"))

        for data, message in samples:
            with self.subTest(message=message):
                self.assertIn(message, taxontoy.validate_document(data))
                with tempfile.TemporaryDirectory() as tmp:
                    path = write_json(tmp, "table.json", data)
                    result = run(["validate", str(path), "--json"])
                self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
                self.assertIn(message, result.stdout)

    def test_remaining_property_branches(self):
        for bad in (
            "nope", "abcd-ef-gh", "2026/11/15", "+026-11-15", "2026-11-1",
            "20261115", "2026-11-15\n", "2026-W01-1", "2026-02-31",
            "２０２６-１１-１５", "٢٠٢٦-١١-١٥", None, 20261115,
        ):
            self.assertFalse(taxontoy._iso_date(bad), bad)
        self.assertTrue(taxontoy._iso_date("2026-11-15"))
        for bad_since in ("２０２６-１１-１５", "٢٠٢٦-١١-١٥"):
            bad_doc = minimal_table()
            bad_doc["properties"]["classes"]["hierarchy"] = {"transitive": _evidence(since=bad_since)}
            self.assertEqual(
                taxontoy.validate_document(bad_doc),
                ["properties.classes.hierarchy.transitive: invalid since"],
            )
        self.assertIsNone(taxontoy._class_of(minimal_table(), "blocks"))
        bare = minimal_table()
        bare["properties"]["relations"]["dependsOn"] = {"transitive": _evidence()}
        self.assertIsNone(taxontoy._inverse_partner(bare, "dependsOn"))

        samples = []
        removed = minimal_table()
        del removed["properties"]
        samples.append(removed)
        not_object = minimal_table()
        not_object["properties"] = []
        samples.append(not_object)
        extra = minimal_table()
        extra["properties"]["extra"] = {}
        samples.append(extra)
        missing_classes = minimal_table()
        del missing_classes["properties"]["classes"]
        samples.append(missing_classes)
        bad_classes = minimal_table()
        bad_classes["properties"]["classes"] = []
        samples.append(bad_classes)
        bad_relations = minimal_table()
        bad_relations["properties"]["relations"] = []
        samples.append(bad_relations)
        unknown_class = minimal_table()
        unknown_class["properties"]["classes"]["nope"] = {}
        samples.append(unknown_class)
        class_list = minimal_table()
        class_list["properties"]["classes"]["hierarchy"] = []
        samples.append(class_list)
        class_flag = minimal_table()
        class_flag["properties"]["classes"]["hierarchy"] = {"reflexive": _evidence()}
        samples.append(class_flag)
        class_both = minimal_table()
        class_both["properties"]["classes"]["hierarchy"] = {"symmetric": _evidence(), "acyclic": _evidence()}
        self.assertEqual(
            taxontoy.validate_document(class_both),
            ["class hierarchy: symmetric and acyclic contradict"],
        )
        unknown_relation = minimal_table()
        unknown_relation["properties"]["relations"]["ghost"] = {}
        samples.append(unknown_relation)
        relation_list = minimal_table()
        relation_list["properties"]["relations"]["dependsOn"] = []
        samples.append(relation_list)
        not_decl = minimal_table()
        not_decl["properties"]["relations"]["dependsOn"] = {"inverse_of": True}
        samples.append(not_decl)
        extra_field = minimal_table()
        extra_field["properties"]["relations"]["dependsOn"] = {
            "inverse_of": {"relation": "blocks", "note": "x", **_evidence()}
        }
        extra_field["properties"]["relations"]["blocks"] = {
            "inverse_of": {"relation": "dependsOn", **_evidence()}
        }
        samples.append(extra_field)
        missing_relation = minimal_table()
        missing_relation["properties"]["relations"]["dependsOn"] = {"inverse_of": _evidence()}
        samples.append(missing_relation)
        padded = minimal_table()
        padded["properties"]["classes"]["hierarchy"] = {"transitive": {"since": "2026-11-15", "reason": " padded "}}
        samples.append(padded)
        empty_disjoint = minimal_table()
        empty_disjoint["properties"]["relations"]["dependsOn"] = {"disjoint_with": []}
        samples.append(empty_disjoint)
        unknown_disjoint = minimal_table()
        unknown_disjoint["properties"]["relations"]["dependsOn"] = {
            "disjoint_with": [{"relation": "ghost", **_evidence()}]
        }
        samples.append(unknown_disjoint)
        unsorted = minimal_table()
        unsorted["properties"]["relations"]["dependsOn"] = {"disjoint_with": [
            {"relation": "similarTo", **_evidence()},
            {"relation": "blocks", **_evidence()},
        ]}
        samples.append(unsorted)
        for data in samples:
            self.assertTrue(taxontoy.validate_document(data))

        only_symmetric = minimal_table()
        only_symmetric["properties"]["relations"]["duplicates"] = {"symmetric": _evidence()}
        self.assertEqual(taxontoy.validate_document(only_symmetric), [])
        only_acyclic = minimal_table()
        only_acyclic["properties"]["relations"]["duplicates"] = {"acyclic": _evidence()}
        self.assertEqual(taxontoy.validate_document(only_acyclic), [])
        both_on_relation = minimal_table()
        both_on_relation["properties"]["relations"]["duplicates"] = {
            "acyclic": _evidence(), "symmetric": _evidence(),
        }
        self.assertEqual(
            taxontoy.validate_document(both_on_relation),
            ["relation duplicates: symmetric and acyclic contradict"],
        )
        split_flags = minimal_table()
        split_flags["properties"]["classes"]["dependency"] = {"symmetric": _evidence()}
        split_flags["properties"]["relations"]["dependsOn"] = {"acyclic": _evidence()}
        self.assertEqual(
            taxontoy.validate_document(split_flags),
            ["dependsOn: symmetric and acyclic contradict"],
        )

        missing_classes = minimal_table()
        del missing_classes["properties"]["classes"]
        self.assertEqual(taxontoy.validate_document(missing_classes), ["properties: missing classes"])
        missing_relations = minimal_table()
        del missing_relations["properties"]["relations"]
        self.assertEqual(taxontoy.validate_document(missing_relations), ["properties: missing relations"])
        class_body = minimal_table()
        class_body["properties"]["classes"]["hierarchy"] = ["symmetric"]
        self.assertEqual(
            taxontoy.validate_document(class_body),
            ["properties.classes.hierarchy: must be an object"],
        )
        unknown_flag = minimal_table()
        unknown_flag["properties"]["classes"]["hierarchy"] = {"reflexive": "nope"}
        self.assertEqual(
            taxontoy.validate_document(unknown_flag),
            ["properties.classes.hierarchy: unknown property reflexive"],
        )
        missing_inverse_relation = minimal_table()
        missing_inverse_relation["properties"]["relations"]["dependsOn"] = {"inverse_of": _evidence()}
        missing_inverse_relation["properties"]["relations"]["blocks"] = {
            "inverse_of": {"relation": "dependsOn", **_evidence()}
        }
        self.assertEqual(taxontoy.validate_document(missing_inverse_relation), [
            "inverse pairs not mutually declared: blocks dependsOn",
            "properties.relations.dependsOn.inverse_of: missing relation",
        ])
        not_array = minimal_table()
        not_array["properties"]["relations"]["dependsOn"] = {"disjoint_with": "blocks"}
        self.assertEqual(
            taxontoy.validate_document(not_array),
            ["properties.relations.dependsOn.disjoint_with: must be a non-empty array"],
        )
        unsorted = minimal_table()
        unsorted["properties"]["relations"]["dependsOn"] = {"disjoint_with": [
            {"relation": "similarTo", **_evidence()},
            {"relation": "blocks", **_evidence()},
        ]}
        self.assertEqual(
            taxontoy.validate_document(unsorted),
            ["properties.relations.dependsOn.disjoint_with: must be sorted and unique"],
        )
        bad_excluded_type = minimal_table()
        bad_excluded_type["excluded"]["audit"] = {"ghost": 1}
        bad_excluded_type["properties"]["relations"]["ghost"] = {}
        self.assertEqual(taxontoy.validate_document(bad_excluded_type), [
            "excluded.audit: must be an array",
            "properties.relations.ghost: unknown relation ghost",
        ])
        relation_body = minimal_table()
        relation_body["properties"]["relations"]["dependsOn"] = ["transitive"]
        self.assertEqual(
            taxontoy.validate_document(relation_body),
            ["properties.relations.dependsOn: must be an object"],
        )
        unknown_rel_flag = minimal_table()
        unknown_rel_flag["properties"]["relations"]["dependsOn"] = {"reflexive": {"since": "bad"}}
        self.assertEqual(
            taxontoy.validate_document(unknown_rel_flag),
            ["properties.relations.dependsOn: unknown property reflexive"],
        )
        disjoint_missing_rel = minimal_table()
        disjoint_missing_rel["properties"]["relations"]["dependsOn"] = {
            "disjoint_with": [_evidence(), {"relation": "blocks", **_evidence()}]
        }
        self.assertEqual(
            taxontoy.validate_document(disjoint_missing_rel),
            ["properties.relations.dependsOn.disjoint_with[0]: missing relation"],
        )
        bad_rel_acyclic = minimal_table()
        bad_rel_acyclic["properties"]["relations"]["duplicates"] = {"acyclic": {"reason": "missing date"}}
        self.assertEqual(
            taxontoy.validate_document(bad_rel_acyclic),
            ["properties.relations.duplicates.acyclic: missing since"],
        )

        graphs = fixture_graphs()
        graphs["audit"]["edges"].append("not-an-edge")
        with tempfile.TemporaryDirectory() as tmp:
            table = write_json(tmp, "table.json", minimal_table())
            args = ["candidates", str(table), "--json"]
            for wave, payload in graphs.items():
                path = write_json(tmp, f"{wave}.json", payload)
                args.extend(["--graph", f"{wave}={path}"])
            result = run(args)
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertIn("malformed audit: 1", result.stdout)

    def test_checked_in_properties_are_consistent(self):
        data = json.loads(TABLE.read_text(encoding="utf-8"))
        self.assertEqual(taxontoy.validate_document(data), [])
        self.assertEqual(
            data["properties"]["relations"]["dependsOn"]["inverse_of"]["relation"],
            "blocks",
        )
        self.assertIn("symmetric", data["properties"]["classes"]["similarity"])


class CandidateTests(unittest.TestCase):
    def _graphs(self):
        graphs = fixture_graphs()
        graphs["audit"]["nodes"] = [
            {"id": "a", "type": "PullRequest"},
            {"id": "b", "type": "PullRequest"},
            {"id": "c", "type": "PullRequest"},
            {"id": "x", "type": "Observation"},
            {"id": "y", "type": "PullRequest"},
            {"id": "run", "type": "ActionsRun"},
            {"id": "repo", "type": "Repository"},
            {"id": "loose"},
            {"type": "Orphan"},
            "nope",
            {"id": "blank", "type": " "},
        ]
        graphs["audit"]["edges"].extend([
            {"source": "a", "relation": "depends-on", "target": "b"},
            {"source": "a", "relation": "DependsOn", "target": "b"},
            {"source": "run", "relation": "belongsTo", "target": "repo"},
            {"source": "a", "relation": "belongsTo", "target": "repo"},
            {"source": "a", "relation": "belongsTo", "target": "loose"},
            {"source": "missing", "relation": "belongsTo", "target": "repo"},
        ])
        graphs["audit"]["edges"] = [
            edge for edge in graphs["audit"]["edges"] if edge.get("relation") != "observes" or True
        ]
        # belongsTo must be excluded or coverage would fail; candidates do not require a closed partition.
        return graphs

    def _table(self):
        table = minimal_table()
        table["properties"] = {
            "classes": {"similarity": {"symmetric": _evidence(since="2026-11-15", reason="mirrored")}},
            "relations": {
                "dependsOn": {"inverse_of": {"relation": "blocks", **_evidence(reason="reverse of dependsOn")}},
                "blocks": {"inverse_of": {"relation": "dependsOn", **_evidence(reason="reverse of blocks")}},
            },
        }
        table["excluded"]["audit"] = sorted([*table["excluded"]["audit"], "DependsOn", "belongsTo", "depends-on"])
        return table

    def _invoke(self, table, graphs, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(tmp, "table.json", table)
            args = ["candidates", str(path), "--format", "json", *extra]
            for wave, payload in graphs.items():
                graph = write_json(tmp, f"{wave}.json", payload)
                args.extend(["--graph", f"{wave}={graph}"])
            result = run(args)
        return result

    def test_each_candidate_type_is_reported_and_not_folded(self):
        result = self._invoke(self._table(), self._graphs())
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        audit = payload["waves"]["audit"]["candidates"]
        self.assertIn({
            "declared": False,
            "detection": "separator",
            "folded": False,
            "relations": ["DependsOn", "depends-on", "dependsOn", "depends_on"],
        }, audit)
        self.assertIn({
            "declared": False,
            "detection": "case",
            "folded": False,
            "relations": ["DependsOn", "dependsOn"],
        }, audit)
        self.assertIn({
            "declared": True,
            "detection": "inverse-pair",
            "folded": False,
            "relations": ["blocks", "dependsOn"],
        }, audit)
        concepts = payload["waves"]["concepts"]["candidates"]
        self.assertIn({
            "declared": True,
            "detection": "symmetric",
            "folded": False,
            "relations": ["similarTo"],
        }, concepts)
        self.assertEqual(
            audit,
            sorted(audit, key=lambda item: (item["detection"], item["relations"])),
        )
        polysemy = next(item for item in audit if item["detection"] == "polysemy")
        self.assertEqual(polysemy["relations"], ["belongsTo"])
        self.assertFalse(polysemy["declared"])
        self.assertFalse(polysemy["folded"])
        self.assertEqual(polysemy["patterns"], [
            {"edges": 1, "source": "ActionsRun", "target": "Repository"},
            {"edges": 1, "source": "PullRequest", "target": "Repository"},
        ])
        self.assertEqual(
            taxontoy._node_types([None, {"id": "a"}, {"type": "T"}, {"id": "ok", "type": "Kind"}, {"id": "bad", "type": " "}]),
            {"ok": "Kind"},
        )
        self.assertIn("polysemy: skipped; nodes have no type", payload["waves"]["cases"]["notes"])
        self.assertTrue(all(item["folded"] is False for wave in payload["waves"].values() for item in wave["candidates"]))

    def test_flipping_a_declaration_changes_the_result(self):
        table = self._table()
        before = json.loads(self._invoke(table, self._graphs()).stdout)
        table["properties"]["classes"]["similarity"] = {}
        table["properties"]["relations"].pop("dependsOn")
        table["properties"]["relations"].pop("blocks")
        after = json.loads(self._invoke(table, self._graphs()).stdout)
        self.assertNotEqual(before, after)

        def declared(payload, wave, detection, relations):
            for item in payload["waves"][wave]["candidates"]:
                if item["detection"] == detection and item["relations"] == relations:
                    return item["declared"]
            self.fail(f"missing {detection} {relations}")

        self.assertTrue(declared(before, "concepts", "symmetric", ["similarTo"]))
        self.assertFalse(declared(after, "concepts", "symmetric", ["similarTo"]))
        self.assertTrue(declared(before, "audit", "inverse-pair", ["blocks", "dependsOn"]))
        self.assertFalse(declared(after, "audit", "inverse-pair", ["blocks", "dependsOn"]))

    def test_property_candidates_treats_one_sided_inverse_as_undeclared(self):
        table = self._table()
        table["properties"]["relations"].pop("blocks")
        self.assertIn(
            "inverse pairs not mutually declared: blocks dependsOn",
            taxontoy.validate_document(table),
        )
        found, notes = taxontoy.property_candidates(
            table,
            {
                "blocks": [{"source": "b", "target": "a"}],
                "dependsOn": [{"source": "a", "target": "b"}],
            },
            {},
        )
        inverse = next(item for item in found if item["detection"] == "inverse-pair")
        self.assertEqual(inverse["relations"], ["blocks", "dependsOn"])
        self.assertFalse(inverse["declared"])
        self.assertEqual(notes, ["polysemy: skipped; nodes have no type"])

    def test_dropping_a_mirrored_edge_removes_the_symmetric_candidate(self):
        graphs = self._graphs()
        before = json.loads(self._invoke(self._table(), graphs).stdout)
        graphs["concepts"]["edges"] = [
            edge for edge in graphs["concepts"]["edges"]
            if not (edge.get("kind") == "similarTo" and edge["source"] == "n")
        ]
        after = json.loads(self._invoke(self._table(), graphs).stdout)
        self.assertNotEqual(before, after)
        names = [item["relations"] for item in after["waves"]["concepts"]["candidates"] if item["detection"] == "symmetric"]
        self.assertNotIn(["similarTo"], names)

    def test_candidates_text_lists_declared_and_notes(self):
        result = self._invoke(self._table(), self._graphs(), "--format", "text")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("wave audit", result.stdout)
        self.assertIn("wave concepts", result.stdout)
        self.assertIn("wave cases", result.stdout)
        self.assertIn(
            "candidate polysemy declared no belongsTo ActionsRun->Repository:1 PullRequest->Repository:1",
            result.stdout,
        )
        self.assertIn("candidate separator declared no DependsOn depends-on dependsOn depends_on", result.stdout)
        self.assertIn("candidate inverse-pair declared yes blocks dependsOn", result.stdout)
        self.assertIn("candidate symmetric declared yes similarTo", result.stdout)
        self.assertIn("note polysemy: skipped; nodes have no type", result.stdout)

    def test_padded_table_argument_is_stripped(self):
        result = run(["validate", "  otl-relations.json  ", "--json"])
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["inputs"][0]["path"], "otl-relations.json")

    def test_blank_onto_argument_uses_the_environment(self):
        if not ONTO:
            self.fail("set ONTO_CHECKOUT to the pinned ONTO checkout")
        with tempfile.TemporaryDirectory() as tmp:
            table = write_json(tmp, "table.json", minimal_table())
            args = ["coverage", str(table), "--onto", "   ", "--json"]
            for wave, payload in fixture_graphs().items():
                path = write_json(tmp, f"{wave}.json", payload)
                args.extend(["--graph", f"{wave}=  {path}  "])
            env = os.environ.copy()
            env["ONTO_CHECKOUT"] = str(Path(ONTO).resolve())
            result = run(args, env=env)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


def _alias(detection, relations):
    return {"detection": detection, "folded": False, "relations": relations}


def ranking_edges():
    return [
        {"source": "a", "relation": "dependsOn", "target": "b"},
        {"source": "a", "relation": "depends_on", "target": "b"},
        {"source": "b", "relation": "blocks", "target": "a"},
        {"source": "a", "relation": "DependsOn", "target": "b"},
        {"source": "a", "relation": "duplicates", "target": "c"},
        {"source": "a", "relation": "overlapsWith", "target": "c"},
        {"source": "p", "kind": "zeta", "target": "q"},
        {"source": "p", "kind": "zeta", "target": "r"},
        {"kind": "zeta", "source": "p", "target": None},
        {"source": "m", "kind": "alpha", "target": "n"},
        {"relation": "alpha", "source": None, "target": "n"},
        {"relation": "alpha", "source": "m", "target": None},
        {"kind": "narrower", "source": "parent", "target": "child"},
        {"relation": "observes", "source": "x", "target": "y"},
        {"kind": "kindWins", "relation": "relationLoses", "source": "s", "target": "t"},
        {"kind": "  nope", "relation": "hidden", "source": "a", "target": "b"},
        {"relation": " padded", "source": "a", "target": "b"},
        {"source": "a", "target": "b"},
        ["not", "edge"],
        {"kind": "", "source": "a", "target": "b"},
        {"relation": None, "source": "a", "target": "b"},
        ["kind"],
        "kind",
    ]


def expected_audit_ranking():
    return [
        {
            "alias_candidates": [], "edges": 3, "elsewhere": None, "folded": False,
            "rank": 1, "relation": "alpha", "status": "unclassified",
        },
        {
            "alias_candidates": [], "edges": 3, "elsewhere": None, "folded": False,
            "rank": 2, "relation": "zeta", "status": "unclassified",
        },
        {
            "alias_candidates": [
                _alias("case", ["DependsOn", "dependsOn"]),
                _alias("inverse-pair", ["DependsOn", "blocks"]),
                _alias("separator", ["DependsOn", "dependsOn", "depends_on"]),
            ],
            "edges": 1, "elsewhere": None, "folded": False,
            "rank": 3, "relation": "DependsOn", "status": "unclassified",
        },
        {
            "alias_candidates": [
                _alias("inverse-pair", ["DependsOn", "blocks"]),
                _alias("inverse-pair", ["blocks", "dependsOn"]),
                _alias("inverse-pair", ["blocks", "depends_on"]),
            ],
            "edges": 1, "elsewhere": None, "folded": False,
            "rank": 4, "relation": "blocks", "status": "excluded",
        },
        {
            "alias_candidates": [
                _alias("inverse-pair", ["blocks", "depends_on"]),
                _alias("separator", ["DependsOn", "dependsOn", "depends_on"]),
            ],
            "edges": 1, "elsewhere": None, "folded": False,
            "rank": 5, "relation": "depends_on", "status": "excluded",
        },
        {
            "alias_candidates": [], "edges": 1, "elsewhere": None, "folded": False,
            "rank": 6, "relation": "kindWins", "status": "unclassified",
        },
        {
            "alias_candidates": [], "edges": 1, "elsewhere": "concepts", "folded": False,
            "rank": 7, "relation": "narrower", "status": "unclassified",
        },
        {
            "alias_candidates": [], "edges": 1, "elsewhere": None, "folded": False,
            "rank": 8, "relation": "observes", "status": "excluded",
        },
        {
            "alias_candidates": [], "edges": 1, "elsewhere": None, "folded": False,
            "rank": 9, "relation": "overlapsWith", "status": "excluded",
        },
    ]


class RankUnclassifiedTests(unittest.TestCase):
    def test_names_are_ranked_without_folding_and_nameless_edges_stay(self):
        found = taxontoy.rank_unclassified(minimal_table(), ranking_edges(), "audit")
        self.assertEqual(found["counts"], {
            "classified": 2, "edges": 23, "excluded": 4, "nameless": 8, "unclassified": 9,
        })
        self.assertEqual(found["nameless"], {"edges": 8})
        self.assertEqual(
            found["counts"]["classified"] + found["counts"]["excluded"]
            + found["counts"]["unclassified"] + found["counts"]["nameless"],
            len(ranking_edges()),
        )
        self.assertEqual(
            sum(row["edges"] for row in found["ranking"]),
            found["counts"]["excluded"] + found["counts"]["unclassified"],
        )
        self.assertEqual(found["ranking"], expected_audit_ranking())
        self.assertEqual(
            [row["relation"] for row in found["ranking"]],
            ["alpha", "zeta", "DependsOn", "blocks", "depends_on", "kindWins", "narrower", "observes", "overlapsWith"],
        )
        depends_on = next(row for row in found["ranking"] if row["relation"] == "depends_on")
        self.assertEqual(depends_on["edges"], 1)
        self.assertFalse(depends_on["folded"])
        self.assertNotIn("relationLoses", [row["relation"] for row in found["ranking"]])
        self.assertNotIn("hidden", [row["relation"] for row in found["ranking"]])
        self.assertTrue(all(row["folded"] is False for row in found["ranking"]))
        self.assertTrue(all(alias["folded"] is False for row in found["ranking"] for alias in row["alias_candidates"]))

    def test_another_profile_does_not_reuse_this_profile_status(self):
        found = taxontoy.rank_unclassified(minimal_table(), ranking_edges(), "concepts")
        self.assertEqual(found["counts"], {
            "classified": 1, "edges": 23, "excluded": 0, "nameless": 8, "unclassified": 14,
        })
        relations = {row["relation"]: row for row in found["ranking"]}
        self.assertNotIn("narrower", relations)
        self.assertEqual(relations["dependsOn"]["status"], "unclassified")
        self.assertEqual(relations["dependsOn"]["elsewhere"], "audit")
        self.assertEqual(relations["dependsOn"]["edges"], 1)
        self.assertEqual(relations["duplicates"]["elsewhere"], "audit")
        self.assertEqual(relations["blocks"]["status"], "unclassified")
        self.assertIsNone(relations["blocks"]["elsewhere"])
        self.assertEqual(sum(row["edges"] for row in found["ranking"]), 14)

    def test_a_missing_endpoint_is_not_part_of_an_alias_pair(self):
        edges = [
            {"source": "a", "relation": "foo", "target": "b"},
            {"source": "c", "relation": "foo", "target": None},
            {"source": "b", "relation": "bar", "target": "a"},
        ]
        found = taxontoy.rank_unclassified(minimal_table(), edges, "audit")
        rows = {row["relation"]: row for row in found["ranking"]}
        self.assertEqual(rows["foo"]["edges"], 2)
        self.assertEqual(rows["bar"]["edges"], 1)
        pair = _alias("inverse-pair", ["bar", "foo"])
        self.assertIn(pair, rows["foo"]["alias_candidates"])
        self.assertIn(pair, rows["bar"]["alias_candidates"])

    def test_non_dict_edges_with_kind_text_stay_nameless(self):
        found = taxontoy.rank_unclassified(minimal_table(), [["kind"], "kind", ["not", "edge"]], "audit")
        self.assertEqual(found["counts"], {
            "classified": 0, "edges": 3, "excluded": 0, "nameless": 3, "unclassified": 0,
        })
        self.assertEqual(found["ranking"], [])

    def test_unknown_profile_and_empty_graph(self):
        with self.assertRaisesRegex(ValueError, "unknown profile"):
            taxontoy.rank_unclassified(minimal_table(), [], "nope")
        found = taxontoy.rank_unclassified(
            minimal_table(),
            [{"source": "a", "relation": "dependsOn", "target": "b"}],
            "audit",
        )
        self.assertEqual(found["ranking"], [])
        self.assertEqual(found["counts"], {
            "classified": 1, "edges": 1, "excluded": 0, "nameless": 0, "unclassified": 0,
        })
        self.assertEqual(found["nameless"], {"edges": 0})

    def test_cli_json_is_deterministic_and_records_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            table = write_json(tmp, "table.json", minimal_table())
            graph = write_json(tmp, "graph.json", {"edges": ranking_edges()})
            args = ["rank-unclassified", str(graph), str(table), "--profile", "audit"]
            first = run([*args, "--format", "json"])
            second = run([*args, "--json"])
            expected_inputs = sorted([
                {"path": str(graph), "sha256": hashlib.sha256(graph.read_bytes()).hexdigest()},
                {"path": str(table), "sha256": hashlib.sha256(table.read_bytes()).hexdigest()},
            ], key=lambda item: item["path"])
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.assertEqual(first.stdout, second.stdout)
        payload = json.loads(first.stdout)
        self.assertEqual(
            first.stdout,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        self.assertEqual(payload["command"], "rank-unclassified")
        self.assertEqual(payload["tool"], "taxontoy")
        self.assertEqual(payload["version"], taxontoy.__version__)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["profile"], "audit")
        self.assertEqual(payload["ranking"], expected_audit_ranking())
        self.assertEqual(payload["inputs"], expected_inputs)

    def test_cli_text_lists_aliases_elsewhere_and_the_nameless_bucket(self):
        with tempfile.TemporaryDirectory() as tmp:
            table = write_json(tmp, "table.json", minimal_table())
            graph = write_json(tmp, "graph.json", {"edges": ranking_edges()})
            result = run([
                "rank-unclassified", str(graph), str(table), "--profile", "audit", "--format", "text",
            ])
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("command rank-unclassified", result.stdout)
        self.assertIn("profile audit", result.stdout)
        self.assertIn(
            "edges 23 classified 2 excluded 4 unclassified 9 nameless 8",
            result.stdout,
        )
        self.assertIn("rank 1 alpha edges 3 status unclassified", result.stdout)
        self.assertIn("rank 2 zeta edges 3 status unclassified", result.stdout)
        self.assertIn("alias separator DependsOn dependsOn depends_on", result.stdout)
        self.assertIn("alias inverse-pair blocks depends_on", result.stdout)
        self.assertIn("rank 7 narrower edges 1 status unclassified elsewhere concepts", result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], "nameless 8")
        self.assertNotIn("relationLoses", result.stdout)
        self.assertNotIn("hidden", result.stdout)

    def test_empty_ranking_text_still_reports_the_nameless_bucket(self):
        with tempfile.TemporaryDirectory() as tmp:
            table = write_json(tmp, "table.json", minimal_table())
            graph = write_json(tmp, "graph.json", {"edges": [
                {"source": "a", "relation": "dependsOn", "target": "b"},
            ]})
            result = run([
                "rank-unclassified", str(graph), str(table), "--profile", "audit", "--format", "text",
            ])
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("edges 1 classified 1 excluded 0 unclassified 0 nameless 0", result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], "nameless 0")
        self.assertNotIn("rank ", result.stdout)

    def test_output_path_is_the_only_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            table = write_json(tmp, "table.json", minimal_table())
            graph = write_json(tmp, "graph.json", {"edges": ranking_edges()})
            args = ["rank-unclassified", str(graph), str(table), "--profile", "audit", "--json"]
            before = set(tmp.iterdir())
            quiet = run(args, cwd=tmp)
            self.assertEqual(quiet.returncode, 0, quiet.stderr + quiet.stdout)
            self.assertEqual(set(tmp.iterdir()), before)
            out = tmp / "ranking.json"
            written = run([*args, "--output", str(out)], cwd=tmp)
            self.assertEqual(written.returncode, 0)
            self.assertEqual(written.stdout, "")
            self.assertEqual(set(tmp.iterdir()), before | {out})
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["profile"], "audit")

    def test_table_can_come_from_the_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            table = write_json(tmp, "table.json", minimal_table())
            graph = write_json(tmp, "graph.json", {"edges": ranking_edges()})
            env = os.environ.copy()
            env["TAXONTOY_TABLE"] = f"  {table}  "
            result = run([
                "rank-unclassified", f"  {graph}  ", "--profile", "concepts", "--format", "json",
            ], env=env)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["profile"], "concepts")
        self.assertEqual(payload["counts"]["classified"], 1)
        self.assertEqual(payload["inputs"][0]["path"], str(graph))

    def test_bad_inputs_fail_without_writing(self):
        env = os.environ.copy()
        env.pop("TAXONTOY_TABLE", None)
        with tempfile.TemporaryDirectory() as tmp:
            graph = write_json(tmp, "graph.json", {"edges": []})
            missing_table = run(["rank-unclassified", str(graph), "--profile", "audit"], env=env)
            self.assertEqual(missing_table.returncode, 1)
            self.assertIn("pass the table path or set TAXONTOY_TABLE", missing_table.stderr)

            table = write_json(tmp, "table.json", minimal_table())
            blank = run(["rank-unclassified", "   ", str(table), "--profile", "audit"], env=env)
            self.assertEqual(blank.returncode, 1)
            self.assertIn("pass the graph path", blank.stderr)

            missing_graph = run(["rank-unclassified", str(Path(tmp) / "absent.json"), str(table), "--profile", "audit"], env=env)
            self.assertEqual(missing_graph.returncode, 1)
            self.assertIn("taxontoy:", missing_graph.stderr)

            for payload, message in (
                ([], "graph JSON must be an object with an edges array"),
                ({"nodes": []}, "graph JSON must be an object with an edges array"),
                ({"edges": "nope"}, "graph JSON must be an object with an edges array"),
            ):
                path = write_json(tmp, "bad-graph.json", payload)
                result = run(["rank-unclassified", str(path), str(table), "--profile", "audit"], env=env)
                self.assertEqual(result.returncode, 1, payload)
                self.assertIn(message, result.stderr)

            array = Path(tmp) / "array.json"
            array.write_text("[]\n", encoding="utf-8")
            invalid = run([
                "rank-unclassified", str(Path(tmp) / "absent.json"), str(array), "--profile", "audit", "--json",
            ], env=env)
            self.assertEqual(invalid.returncode, 1)
            body = json.loads(invalid.stdout)
            self.assertEqual(body["command"], "rank-unclassified")
            self.assertFalse(body["ok"])
            self.assertIn("table must be a JSON object", body["errors"])
            self.assertNotIn("ranking", body)

            conflict = run([
                "rank-unclassified", str(graph), str(table), "--profile", "audit", "--json", "--format", "text",
            ], env=env)
            self.assertEqual(conflict.returncode, 1)
            self.assertIn("disagree", conflict.stderr)

            omitted = run(["rank-unclassified", str(graph), str(table)], env=env)
            self.assertEqual(omitted.returncode, 2)

        usage = run(["rank-unclassified", "--profile", "nope"])
        self.assertEqual(usage.returncode, 2)


class CommittedRankingTests(unittest.TestCase):
    def _report(self, profile):
        path = ROOT / f"ranking-{profile}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        self.assertEqual(payload["command"], "rank-unclassified")
        self.assertEqual(payload["version"], taxontoy.__version__)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["profile"], profile)
        coverage = json.loads((ROOT / "coverage.json").read_text(encoding="utf-8"))
        graph = f"build/{profile}.json"
        self.assertEqual(
            next(item for item in payload["inputs"] if item["path"] == graph),
            next(item for item in coverage["inputs"] if item["path"] == graph),
        )
        counts = payload["counts"]
        self.assertEqual(
            counts["classified"] + counts["excluded"] + counts["unclassified"] + counts["nameless"],
            counts["edges"],
        )
        self.assertEqual(
            sum(row["edges"] for row in payload["ranking"]),
            counts["excluded"] + counts["unclassified"],
        )
        self.assertEqual(payload["nameless"]["edges"], counts["nameless"])
        self.assertTrue(all(row["folded"] is False for row in payload["ranking"]))
        self.assertTrue(all(
            alias["folded"] is False and alias["detection"] in {"case", "inverse-pair", "separator"}
            for row in payload["ranking"] for alias in row["alias_candidates"]
        ))
        return payload

    def test_real_graphs_rank_excluded_names_and_keep_a_nameless_bucket(self):
        audit = self._report("audit")
        self.assertEqual(audit["counts"], {
            "classified": 38, "edges": 2342, "excluded": 2304, "nameless": 0, "unclassified": 0,
        })
        self.assertEqual(
            [(row["rank"], row["relation"], row["edges"]) for row in audit["ranking"][:10]],
            [
                (1, "belongsTo", 691),
                (2, "changesFile", 564),
                (3, "updates", 564),
                (4, "hasRun", 209),
                (5, "runsAt", 93),
                (6, "overlapsWith", 25),
                (7, "derived_from", 14),
                (8, "hasBase", 14),
                (9, "hasHead", 14),
                (10, "hasMain", 14),
            ],
        )
        self.assertTrue(all(row["status"] == "excluded" and row["elsewhere"] is None for row in audit["ranking"]))
        depends_on = next(row for row in audit["ranking"] if row["relation"] == "depends_on")
        self.assertEqual(depends_on["edges"], 13)
        self.assertIn(
            _alias("separator", ["dependsOn", "depends_on"]),
            depends_on["alias_candidates"],
        )
        self.assertIn(
            _alias("inverse-pair", ["blocks", "depends_on"]),
            depends_on["alias_candidates"],
        )

        concepts = self._report("concepts")
        self.assertEqual(concepts["counts"], {
            "classified": 91, "edges": 826, "excluded": 735, "nameless": 0, "unclassified": 0,
        })
        self.assertEqual(
            [(row["relation"], row["edges"]) for row in concepts["ranking"]],
            [
                ("hasElement", 408),
                ("genusElement", 114),
                ("hasName", 84),
                ("derived_from", 43),
                ("differentiaElement", 43),
                ("observes", 43),
            ],
        )

        cases = self._report("cases")
        self.assertEqual(cases["counts"], {
            "classified": 10, "edges": 10, "excluded": 0, "nameless": 0, "unclassified": 0,
        })
        self.assertEqual(cases["ranking"], [])


class MutationTests(unittest.TestCase):
    def test_rank_mutations_of_the_real_source_are_killed(self):
        source = (ROOT / "taxontoy.py").read_text(encoding="utf-8")
        mutations = [
            ('(-row["edges"], row["relation"])', '(row["edges"], row["relation"])'),
            ('(-row["edges"], row["relation"])', '(-row["edges"], row["relation"][::-1])'),
            ("enumerate(ranking, start=1)", "enumerate(ranking, start=0)"),
            (
                '"excluded" if name in excluded else "unclassified"',
                '"unclassified" if name in excluded else "excluded"',
            ),
            (
                '"folded": False,\n            "relation": name,',
                '"folded": True,\n            "relation": name,',
            ),
            ("nameless += 1", "nameless += 0"),
            (
                'if item["detection"] in _ALIAS_DETECTIONS',
                'if item["detection"] == "case"',
            ),
            (
                '_ALIAS_DETECTIONS = ("case", "inverse-pair", "separator")',
                '_ALIAS_DETECTIONS = ("case", "inverse-pair", "same-pairs", "separator")',
            ),
            (
                'related = [dict(item) for item in aliases if name in item["relations"]]',
                "related = []",
            ),
            (
                '"elsewhere": _elsewhere(table, profile, name),',
                '"elsewhere": None,',
            ),
            (
                '"edges": classified + excluded_edges + unclassified + nameless,',
                '"edges": classified + unclassified + nameless,',
            ),
            (
                "if _name_ok(source) and _name_ok(target):",
                "if _name_ok(source) or _name_ok(target):",
            ),
            (
                "if _name_ok(source) and _name_ok(target):",
                "if True:",
            ),
            (
                "if not isinstance(edge, dict):\n        return None",
                "if False:\n        return None",
            ),
            (
                'rank.add_argument("--profile", required=True, choices=WAVES)',
                'rank.add_argument("--profile", required=False, choices=WAVES)',
            ),
            (
                '"edges": len(items),',
                '"edges": len(items) + len(related),',
            ),
            ("if profile not in WAVES:", "if False and profile not in WAVES:"),
            ("            return wave\n", "            return None\n"),
            (
                "if name in classified_names:\n            continue",
                "if False and name in classified_names:\n            continue",
            ),
            (
                'if not _name_ok(name):\n        return None\n    return name',
                'if not _name_ok(name):\n        return None\n    return name.casefold()',
            ),
            ("if not graph_path:", "if graph_path:"),
            (
                'f"nameless {payload[\'nameless\'][\'edges\']}"',
                '"nameless 0"',
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            table_path = write_json(tmp, "table.json", minimal_table())
            graph_path = write_json(tmp, "graph.json", {"edges": ranking_edges()})
            golden = _ranking_snapshot(taxontoy, graph_path, table_path)
            killed = []
            survived = []
            for old, new in mutations:
                self.assertEqual(source.count(old), 1, old)
                mutated = source.replace(old, new, 1)
                try:
                    module = _load_mutant(mutated)
                    found = _ranking_snapshot(module, graph_path, table_path)
                except Exception:
                    killed.append(old)
                    continue
                if found != golden:
                    killed.append(old)
                else:
                    survived.append(old)
        sys.stderr.write(f"mutation killed={len(killed)} survived={len(survived)}\n")
        self.assertEqual(survived, [], survived)


def _ranking_snapshot(module, graph_path, table_path):
    import io
    from contextlib import redirect_stderr, redirect_stdout

    table = json.loads(Path(table_path).read_text(encoding="utf-8"))
    edges = json.loads(Path(graph_path).read_text(encoding="utf-8"))["edges"]
    try:
        module.rank_unclassified(table, edges, "nope")
        bad = None
    except ValueError as exc:
        bad = str(exc)
    buffer = io.StringIO()
    with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
        code = module.main([
            "rank-unclassified", str(graph_path), str(table_path), "--profile", "audit", "--format", "text",
        ])
    with redirect_stderr(io.StringIO()):
        try:
            omitted = module.main(["rank-unclassified", str(graph_path), str(table_path)])
        except SystemExit as exc:
            omitted = exc.code if isinstance(exc.code, int) else 1
    endpoints = [
        {"source": "a", "relation": "foo", "target": "b"},
        {"source": "c", "relation": "foo", "target": None},
        {"source": "b", "relation": "bar", "target": "a"},
    ]
    return {
        "audit": module.rank_unclassified(table, edges, "audit"),
        "bad": bad,
        "code": code,
        "concepts": module.rank_unclassified(table, edges, "concepts"),
        "endpoints": module.rank_unclassified(table, endpoints, "audit"),
        "omitted": omitted,
        "text": buffer.getvalue(),
    }


def _load_mutant(text):
    import importlib.util

    directory = Path(tempfile.mkdtemp(prefix="taxontoy-mutant-"))
    path = directory / "mutant_rank.py"
    path.write_text(text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"mutant_rank_{directory.name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SHA_A = "a" * 40
_SHA_B = "b" * 40
_SHA_C = "c" * 40


def _source(repo, commit, path, role, file):
    return {"commit": commit, "file": file, "path": path, "repo": repo, "role": role}


def _write_manifest(directory, sources, name="manifest.json"):
    return write_json(directory, name, {"sources": sources})


class RepoRelationTests(unittest.TestCase):
    def test_checked_in_graph_has_no_double_distribution(self):
        graph_path = ROOT / "fixtures" / "repos" / "graph.json"
        report_path = ROOT / "repos-properties.json"
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(
            graph_path.read_text(encoding="utf-8"),
            json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        self.assertEqual(graph["version"], taxontoy.__version__)
        graph_sha = hashlib.sha256(graph_path.read_bytes()).hexdigest()
        recorded = next(
            item["sha256"] for item in report["inputs"] if item["path"] == "fixtures/repos/graph.json"
        )
        self.assertEqual(recorded, graph_sha)
        self.assertEqual(
            report_path.read_text(encoding="utf-8"),
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        self.assertEqual(report["command"], "properties")
        self.assertEqual(report["version"], taxontoy.__version__)
        self.assertTrue(report["ok"])
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["skipped"], 0)
        self.assertEqual(report["violations"], [])
        self.assertEqual(report["double_distribution"], {"count": 0, "pairs": []})
        self.assertEqual(report["counts"], {
            "consumes": 16, "subtree": 6, "vendor": 25, "workflow-pin": 12,
        })
        self.assertEqual(
            [(item["proposed"], item["relation"]) for item in graph["mapping"]],
            [
                ("subtree-of", "subtree"),
                ("vendors", "vendor"),
                ("pins", "workflow-pin"),
                ("consumes", "consumes"),
            ],
        )
        edges = graph["edges"]
        self.assertIn({
            "kind": "subtree",
            "path": "blueprobe",
            "provenance": {
                "commit": "320126983a992e30730350c5e9b0e4051c0d33d4",
                "path": "child-heads.json",
                "repo": "myon-bioinformatics/Aoi",
            },
            "source": "myon-bioinformatics/aoi",
            "target": "myon-bioinformatics/blueprobe",
        }, edges)
        self.assertTrue(any(
            edge["kind"] == "vendor"
            and edge["source"] == "myon-bioinformatics/aoi"
            and edge["target"] == "myon-bioinformatics/markdown"
            and edge["path"] == "vendor/markdown.py"
            and edge["provenance"]["path"] == "vendor.lock.json"
            for edge in edges
        ))
        self.assertTrue(any(
            edge["kind"] == "vendor"
            and edge["source"] == "myon-bioinformatics/blueprobe"
            and edge["provenance"]["path"] == "blueprobe/vendor.lock.json"
            for edge in edges
        ))
        self.assertTrue(any(
            edge["kind"] == "workflow-pin"
            and             edge["source"] == "myon-bioinformatics/topontology"
            and edge["target"] == "myon-bioinformatics/onto"
            and edge["pin"] == "8ca1ee5dc8c2714ddcdea6db6774f7168c0e617b"
            for edge in edges
        ))
        self.assertTrue(any(
            edge["kind"] == "consumes"
            and edge["source"] == "myon-bioinformatics/queryu"
            and edge["target"] == "myon-bioinformatics/blueprobe"
            and edge["origin"] == "blueprobe"
            for edge in edges
        ))
        self.assertNotIn("5c138352", graph_path.read_text(encoding="utf-8"))
        rebuilt = run(["graph", "--manifest", "fixtures/repos/manifest.json", "--format", "json"])
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        self.assertEqual(json.loads(rebuilt.stdout)["edges"], edges)
        checked = run([
            "properties", "otl-relations.json", "--graph", "fixtures/repos/graph.json", "--format", "text",
        ])
        self.assertEqual(checked.returncode, 0, checked.stderr + checked.stdout)
        self.assertIn("double_distribution 0", checked.stdout)
        checked_json = run([
            "properties", "otl-relations.json", "--graph", "fixtures/repos/graph.json", "--format", "json",
        ])
        self.assertEqual(checked_json.returncode, 0, checked_json.stderr + checked_json.stdout)
        live = json.loads(checked_json.stdout)
        live_sha = next(
            item["sha256"] for item in live["inputs"] if item["path"] == "fixtures/repos/graph.json"
        )
        self.assertEqual(live["version"], taxontoy.__version__)
        self.assertEqual(live_sha, graph_sha)
        self.assertEqual(live_sha, recorded)
        graph_text = run(["graph", "--manifest", "fixtures/repos/manifest.json", "--format", "text"])
        self.assertEqual(graph_text.returncode, 0, graph_text.stderr)
        self.assertTrue(json.loads(rebuilt.stdout)["ok"])
        self.assertIn(f"nodes {len(graph['nodes'])}", graph_text.stdout)
        self.assertIn(f"edges {len(edges)}", graph_text.stdout)
        self.assertIn(
            "edge subtree myon-bioinformatics/aoi myon-bioinformatics/blueprobe path blueprobe",
            graph_text.stdout,
        )
        self.assertIn("mapping subtree-of subtree", graph_text.stdout)
        self.assertIn("mapping pins workflow-pin", graph_text.stdout)

    def test_path_overlap_reports_both_edges_and_endpoint_pairs_do_too(self):
        table = minimal_table()
        table["properties"]["relations"]["narrower"] = {
            "disjoint_with": [{"relation": "similarTo", "since": "2026-11-15", "reason": "endpoints differ"}],
        }
        table["properties"]["relations"]["similarTo"] = {
            "disjoint_with": [{"relation": "narrower", "since": "2026-11-15", "reason": "endpoints differ"}],
        }
        table["repos"] = {"subtree": "hierarchy", "vendor": "dependency"}
        table["direction"]["subtree"] = "forward"
        table["direction"]["vendor"] = "forward"
        table["excluded"]["repos"] = []
        table["properties"]["relations"]["subtree"] = {
            "disjoint_with": [{"relation": "vendor", "since": "2026-11-21", "reason": "one distribution"}],
        }
        edges = [
            {"kind": "subtree", "source": "hub", "target": "child", "path": "pkg"},
            {"kind": "vendor", "source": "hub", "target": "up", "path": "pkg/vendor/lib.py"},
            {"kind": "vendor", "source": "other", "target": "up", "path": "pkg/vendor/lib.py"},
            {"kind": "narrower", "source": "p", "target": "c"},
            {"kind": "similarTo", "source": "p", "target": "c"},
            {"kind": "similarTo", "source": "p", "target": "d"},
            {"relation": "vendor", "source": "hub", "target": "up", "path": "pkg"},
            {"kind": "", "source": "hub", "target": "up"},
            "not-an-edge",
        ]
        violations, skipped = taxontoy.property_violations(table, edges)
        self.assertEqual(skipped, 2)
        kinds = {tuple(item["relations"]) for item in violations}
        self.assertEqual(kinds, {("narrower", "similarTo"), ("subtree", "vendor")})
        distributed = [item for item in violations if item["relations"] == ["subtree", "vendor"]]
        self.assertEqual(len(distributed), 2)
        prefixed = next(item for item in distributed if item["pair"].get("prefix") == "pkg")
        self.assertEqual(prefixed["pair"]["repository"], "hub")
        self.assertEqual(prefixed["pair"]["path"], "pkg/vendor/lib.py")
        self.assertEqual({edge["kind"] for edge in prefixed["edges"]}, {"subtree", "vendor"})
        exact = next(item for item in distributed if "prefix" not in item["pair"])
        self.assertEqual(exact["pair"], {"path": "pkg", "repository": "hub"})
        ended = next(item for item in violations if item["relations"] == ["narrower", "similarTo"])
        self.assertEqual(ended["pair"], {"source": "p", "target": "c"})
        self.assertEqual(taxontoy._double_distribution(violations)["count"], 2)
        with tempfile.TemporaryDirectory() as tmp:
            table_path = write_json(tmp, "table.json", table)
            graph_path = write_json(tmp, "graph.json", {"edges": edges, "nodes": []})
            text = run(["properties", str(table_path), "--graph", str(graph_path), "--format", "text"])
            strict = run(["properties", str(table_path), "--graph", str(graph_path), "--strict", "--json"])
        self.assertEqual(text.returncode, 1, text.stderr + text.stdout)
        self.assertIn("violation disjoint_with subtree vendor repository hub path pkg/vendor/lib.py", text.stdout)
        self.assertIn("violation disjoint_with narrower similarTo source p target c", text.stdout)
        self.assertIn("edge subtree hub child path pkg", text.stdout)
        self.assertEqual(strict.returncode, 2)
        self.assertFalse(json.loads(strict.stdout)["ok"])

    def test_manifest_and_record_errors_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            samples = [
                ({}, "non-empty sources"),
                ({"sources": []}, "non-empty sources"),
                ({"sources": "nope"}, "non-empty sources"),
                ({"sources": ["nope"]}, "must be an object"),
                ({"sources": [{"role": "heads"}]}, "missing commit"),
                ({"sources": [_source("hub", "abc", "heads.json", "heads", "heads.json")]}, "40 hex"),
                ({"sources": [_source("hub", _SHA_A, "heads.json", "nope", "heads.json")]}, "unknown role"),
                ({"sources": [_source("hub", _SHA_A, "heads.json", "heads", "/etc/passwd")]}, "under the manifest"),
                ({"sources": [_source("hub", _SHA_A, "heads.json", "heads", "../heads.json")]}, "under the manifest"),
                ({"sources": [_source("hub", _SHA_A, "heads.json", "heads", "missing.json")]}, "file is missing"),
            ]
            for payload, message in samples:
                path = write_json(tmp, "manifest.json", payload)
                result = run(["graph", "--manifest", str(path)])
                self.assertEqual(result.returncode, 1, payload)
                self.assertIn(message, result.stderr)
            write_json(tmp, "bad.json", [])
            array = run(["graph", "--manifest", str(tmp / "bad.json")])
            self.assertIn("non-empty sources", array.stderr)
            (tmp / "dup.json").write_text('{"sources":[{"commit":"a"}]}', encoding="utf-8")
            # duplicate keys are rejected by the loader
            (tmp / "dup.json").write_text(
                '{"schema":"x","schema":"y"}\n', encoding="utf-8",
            )
            self.assertIn("duplicate key", run(["graph", "--manifest", str(tmp / "dup.json")]).stderr)

            heads = {
                "heads": {
                    "": {"optional": {"subtree_prefix": "child"}},
                    "hub/child": [],
                    "hub/plain": {"sha": "1"},
                    "hub/odd": {"optional": []},
                    "hub/bare": {"optional": {}},
                    "hub/deep": {"optional": {"subtree_prefix": "a/b"}},
                    "hub/dot": {"optional": {"subtree_prefix": "."}},
                    "hub/child-a": {"optional": {"subtree_prefix": "same"}},
                    "hub/child-b": {"optional": {"subtree_prefix": "same"}},
                    "hub/only": {"optional": {"subtree_prefix": "only"}},
                },
            }
            write_json(tmp, "heads.json", heads)
            rows = taxontoy._manifest_rows(str(_write_manifest(tmp, [
                _source("hub", _SHA_A, "heads.json", "heads", "heads.json"),
            ])))
            loaded = [{**row, "text": Path(row["full"]).read_text(encoding="utf-8")} for row in rows]
            with self.assertRaises(ValueError) as bad_head:
                taxontoy._prefix_rows(loaded)
            self.assertIn("each head must name a repository", str(bad_head.exception))

            write_json(tmp, "heads.json", ["nope"])
            loaded[0]["text"] = (tmp / "heads.json").read_text(encoding="utf-8")
            with self.assertRaises(ValueError) as bad_doc:
                taxontoy._prefix_rows(loaded)
            self.assertIn("heads must be an object", str(bad_doc.exception))

            write_json(tmp, "heads.json", {"heads": {"hub/only": {"optional": {"subtree_prefix": "only"}}}})
            (tmp / "note.json").write_text("{", encoding="utf-8")
            loaded[0]["text"] = (tmp / "note.json").read_text(encoding="utf-8")
            with self.assertRaises(ValueError) as bad_json:
                taxontoy._prefix_rows(loaded)
            self.assertIn("invalid JSON", str(bad_json.exception))

            (tmp / "heads.json").write_text(
                '{"heads":{"hub/only":{"optional":{"subtree_prefix":"only"}},"hub/only":{}}}\n',
                encoding="utf-8",
            )
            loaded[0]["text"] = (tmp / "heads.json").read_text(encoding="utf-8")
            with self.assertRaises(ValueError) as dup:
                taxontoy._prefix_rows(loaded)
            self.assertIn("duplicate key", str(dup.exception))
            loaded[0]["text"] = '{"heads":{"hub/odd":{"optional":[]}}}\n'
            with self.assertRaises(ValueError) as odd:
                taxontoy._prefix_rows(loaded)
            self.assertIn("optional must be an object", str(odd.exception))
            loaded[0]["text"] = '{"heads":{"hub/bare":{"optional":{}}}}\n'
            self.assertEqual(taxontoy._prefix_rows(loaded), [])
            loaded[0]["text"] = '{"heads":{"hub/plain":{"sha":"1"}}}\n'
            self.assertEqual(taxontoy._prefix_rows(loaded), [])
            loaded[0]["text"] = '{"heads":{"hub/deep":{"optional":{"subtree_prefix":"a/b"}}}}\n'
            with self.assertRaises(ValueError) as deep:
                taxontoy._prefix_rows(loaded)
            self.assertIn("one path segment", str(deep.exception))

    def test_lock_siblings_and_workflow_edges_follow_the_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_json(tmp, "heads.json", {"heads": {
                "hub/left": {"optional": {"subtree_prefix": "left"}},
                "hub/right": {"optional": {"subtree_prefix": "right"}},
                "hub/same-a": {"optional": {"subtree_prefix": "same"}},
                "hub/same-b": {"optional": {"subtree_prefix": "same"}},
            }})
            write_json(tmp, "root.lock.json", {"schema": "vendor-lock/1", "files": [
                {"repository": "up/lib", "source": "lib.py", "destination": "vendor/lib.py"},
            ]})
            write_json(tmp, "child.lock.json", {"schema": "vendor-lock/1", "files": [
                {
                    "repository": "up/lib",
                    "source": "lib.py",
                    "destination": "vendor/lib.py",
                    "commit": _SHA_B,
                },
            ]})
            write_json(tmp, "same.lock.json", {"schema": "nope"})
            (tmp / "siblings.py").write_text(
                "SIBLINGS = {'left': ('right', 'left/self', '.', 'unknown'), 'missing': ('right',)}\n"
                "SIBLINGS['right'] = ('left/core',)\n",
                encoding="utf-8",
            )
            (tmp / "ci.yml").write_text(
                "\n".join([
                    "jobs:",
                    "  a:",
                    "    uses: owner/reusable/.github/workflows/x.yml@" + _SHA_C,
                    "    steps:",
                    "      - uses: ./.github/workflows/local.yml@" + _SHA_A,
                    "      - uses: actions/checkout@" + _SHA_A,
                    "        with:",
                    "          repository: up/lib",
                    "          ref: " + _SHA_B,
                    "      - uses: actions/checkout@" + _SHA_A,
                    "        with:",
                    "          repository: up/lib/extra",
                    "          ref: " + _SHA_B,
                    "      - run: echo",
                    "        with:",
                    "          repository: up/lib",
                    "          ref: " + ("d" * 40),
                ]),
                encoding="utf-8",
            )
            manifest = _write_manifest(tmp, [
                _source("hub", _SHA_A, "heads.json", "heads", "heads.json"),
                _source("hub", _SHA_A, "vendor.lock.json", "lock", "root.lock.json"),
                _source("hub", _SHA_A, "left/vendor.lock.json", "lock", "child.lock.json"),
                _source("hub", _SHA_A, "same/vendor.lock.json", "lock", "same.lock.json"),
                _source("hub", _SHA_A, "aoi.py", "siblings", "siblings.py"),
                _source("hub", _SHA_A, ".github/workflows/ci.yml", "workflow", "ci.yml"),
            ])
            rows = []
            for row in taxontoy._manifest_rows(str(manifest)):
                rows.append({**row, "text": Path(row["full"]).read_text(encoding="utf-8")})
            records = taxontoy._prefix_rows(rows)
            self.assertEqual(taxontoy._lock_holder(rows[1], records), "hub")
            self.assertEqual(taxontoy._lock_holder(rows[2], records), "hub/left")
            with self.assertRaises(ValueError) as clash:
                taxontoy._lock_holder(rows[3], records)
            self.assertIn("more than one repository", str(clash.exception))
            with self.assertRaises(ValueError) as schema:
                taxontoy._lock_edges(rows[3], records)
            self.assertIn("vendor-lock/1", str(schema.exception))
            bad_files = dict(rows[1])
            bad_files["text"] = '{"schema":"vendor-lock/1","files":{}}\n'
            with self.assertRaises(ValueError) as files:
                taxontoy._lock_edges(bad_files, records)
            self.assertIn("files must be an array", str(files.exception))
            bad_row = dict(rows[1])
            bad_row["text"] = '{"schema":"vendor-lock/1","files":["x"]}\n'
            with self.assertRaises(ValueError) as item:
                taxontoy._lock_edges(bad_row, records)
            self.assertIn("must be an object", str(item.exception))
            bad_pin = dict(rows[2])
            bad_pin["text"] = json.dumps({"schema": "vendor-lock/1", "files": [{
                "repository": "up/lib", "source": "lib.py", "destination": "vendor/lib.py", "commit": "nope",
            }]})
            with self.assertRaises(ValueError) as pin:
                taxontoy._lock_edges(bad_pin, records)
            self.assertIn("commit must be 40 hex", str(pin.exception))
            root_edges = taxontoy._lock_edges(rows[1], records)
            self.assertEqual(root_edges[0]["source"], "hub")
            self.assertNotIn("pin", root_edges[0])
            bad_dest = dict(rows[1])
            bad_dest["text"] = json.dumps({"schema": "vendor-lock/1", "files": [{
                "repository": "up/lib", "source": "lib.py", "destination": "../out.py",
            }]})
            with self.assertRaises(ValueError) as dest:
                taxontoy._lock_edges(bad_dest, records)
            self.assertIn("destination", str(dest.exception))
            with self.assertRaises(ValueError):
                taxontoy._consume_edges(rows[4], records)
            (tmp / "siblings.py").write_text(
                "SIBLINGS = {'left': ('right', 'left/self', '.', 'unknown'), 'right': ('left/core',)}\n",
                encoding="utf-8",
            )
            rows[4]["text"] = (tmp / "siblings.py").read_text(encoding="utf-8")
            consumed = taxontoy._consume_edges(rows[4], records)
            pairs = {(edge["source"], edge["target"], edge["origin"]) for edge in consumed}
            self.assertIn(("hub/left", "hub/right", "right"), pairs)
            self.assertIn(("hub/right", "hub/left", "left/core"), pairs)
            self.assertFalse(any(edge["origin"] in {".", "left/self", "unknown"} for edge in consumed))
            pins = {(edge["target"], edge["pin"]) for edge in taxontoy._pin_edges(rows[5])}
            self.assertEqual(pins, {
                ("owner/reusable", _SHA_C),
                ("actions/checkout", _SHA_A),
                ("up/lib", _SHA_B),
            })
            self.assertNotIn(("up/lib", "d" * 40), pins)
            self.assertEqual(sum(1 for edge in taxontoy._pin_edges(rows[5]) if edge["target"] == "actions/checkout"), 1)

            broken = [
                ("SIBLINGS = {'left': object()}\n", "literal"),
                ("def f():\n  pass\n", "not found"),
                ("SIBLINGS = []\n", "must be a dict"),
                ("SIBLINGS['left'] = ('right',)\n", "must be a dict"),
                ("SIBLINGS: int = {'left': ()}\n", "list directories"),
                ("SIBLINGS = {'left': [1]}\n", "non-empty string"),
                ("SIBLINGS = {'nope': ('right',)}\n", "not a subtree prefix"),
                ("(\n", "literal"),
            ]
            for text, message in broken:
                (tmp / "siblings.py").write_text(text, encoding="utf-8")
                rows[4]["text"] = text
                with self.assertRaises(ValueError) as exc:
                    taxontoy._consume_edges(rows[4], records)
                self.assertIn(message, str(exc.exception), text)
            self.assertIsNone(taxontoy._github_repo("only"))
            self.assertIsNone(taxontoy._github_repo(".owner/name"))
            self.assertIsNone(taxontoy._github_repo("owner/.name"))
            self.assertIsNone(taxontoy._github_repo("owner/bad!"))
            self.assertEqual(taxontoy._child_repo("missing", records, "hub"), None)
            with self.assertRaises(ValueError):
                taxontoy._child_repo("same", records, "hub")

    def test_property_declarations_without_a_partner_add_no_hit(self):
        table = {"properties": {"relations": {
            "subtree": {"disjoint_with": ["vendor", {"since": "2026-11-21"}]},
            "vendor": [],
            "other": "nope",
        }}}
        violations, skipped = taxontoy.property_violations(table, [])
        self.assertEqual((violations, skipped), ([], 0))
        self.assertEqual(taxontoy.property_violations({"properties": []}, [])[0], [])
        self.assertEqual(taxontoy.property_violations({}, [])[0], [])
        mixed = [
            {"kind": "subtree", "source": "hub", "target": "child", "path": "pkg"},
            {"kind": "vendor", "source": "hub", "target": "up"},
        ]
        table = {"properties": {"relations": {"subtree": {"disjoint_with": [
            {"relation": "vendor", "since": "2026-11-21", "reason": "pair"},
            {"relation": "subtree", "since": "2026-11-21", "reason": "self"},
        ]}}}}
        violations, skipped = taxontoy.property_violations(table, mixed)
        self.assertEqual(violations, [])
        self.assertEqual(skipped, 0)
        ended = taxontoy._double_distribution([{
            "relations": ["vendor", "subtree"],
            "pair": {"source": "hub", "target": "child"},
        }, {
            "relations": ["subtree", "vendor"],
            "pair": {"source": "hub", "target": "child"},
        }, {
            "relations": ["subtree", "vendor"],
            "pair": {"path": "pkg/lib.py", "prefix": "pkg", "repository": "hub"},
        }, {
            "relations": ["subtree", "vendor"],
            "pair": {"path": "pkg/lib.py", "prefix": "pkg", "repository": "hub"},
        }])
        self.assertEqual(ended, {"count": 2, "pairs": [
            {"path": "pkg/lib.py", "prefix": "pkg", "repository": "hub"},
            {"source": "hub", "target": "child"},
        ]})
        self.assertIsNone(taxontoy._norm_path("././"))
        self.assertEqual(taxontoy._norm_path("./a/b"), "a/b")
        self.assertEqual(taxontoy._norm_path("a/./b"), "a/b")
        self.assertIsNone(taxontoy._norm_path("./"))
        self.assertIsNone(taxontoy._norm_path(" pkg"))
        self.assertIsNone(taxontoy._norm_path("a/../b"))
        self.assertIsNone(taxontoy._norm_path("a//b"))
        self.assertIsNone(taxontoy._holder_path({"source": "hub", "path": " "}))
        self.assertIsNone(taxontoy._holder_path({"path": "pkg"}))
        self.assertFalse(taxontoy._paths_overlap("blue", "blueprobe"))

    def test_cli_accepts_env_paths_and_rejects_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_json(tmp, "heads.json", {"heads": {"hub/child": {"optional": {"subtree_prefix": "child"}}}})
            manifest = _write_manifest(tmp, [_source("hub", _SHA_A, "child-heads.json", "heads", "heads.json")])
            env = os.environ.copy()
            env["TAXONTOY_MANIFEST"] = f"  {manifest}  "
            env.pop("TAXONTOY_GRAPH", None)
            from_env = run(["graph", "--format", "json"], env=env)
            self.assertEqual(from_env.returncode, 0, from_env.stderr)
            self.assertEqual(json.loads(from_env.stdout)["edges"][0]["target"], "hub/child")
            missing = run(["graph"], env={**env, "TAXONTOY_MANIFEST": "   "})
            self.assertEqual(missing.returncode, 1)
            self.assertIn("TAXONTOY_MANIFEST", missing.stderr)
            out = tmp / "out.json"
            written = run(["graph", "--manifest", str(manifest), "--output", str(out)], env=env)
            self.assertEqual(written.returncode, 0)
            self.assertEqual(written.stdout, "")
            self.assertTrue(out.is_file())
            conflict = run(["graph", "--manifest", str(manifest), "--json", "--format", "text"], env=env)
            self.assertEqual(conflict.returncode, 1)
            self.assertIn("disagree", conflict.stderr)

            table = minimal_table()
            table["repos"] = {"subtree": "hierarchy"}
            table["direction"]["subtree"] = "forward"
            table["excluded"]["repos"] = []
            table_path = write_json(tmp, "table.json", table)
            graph = write_json(tmp, "graph.json", {"edges": []})
            env["TAXONTOY_TABLE"] = str(table_path)
            env["TAXONTOY_GRAPH"] = f"  {graph}  "
            env.pop("TAXONTOY_MANIFEST")
            props = run(["properties", "--format", "json"], env=env)
            self.assertEqual(props.returncode, 0, props.stderr + props.stdout)
            self.assertEqual(json.loads(props.stdout)["counts"], {})
            manifest_env = dict(env)
            manifest_env.pop("TAXONTOY_GRAPH", None)
            from_manifest = run(
                ["properties", str(table_path), "--manifest", str(manifest), "--format", "json"],
                env=manifest_env,
            )
            self.assertEqual(from_manifest.returncode, 0, from_manifest.stderr + from_manifest.stdout)
            self.assertEqual(json.loads(from_manifest.stdout)["counts"]["subtree"], 1)
            both = run(["properties", "--graph", str(graph), "--manifest", str(manifest)], env=env)
            self.assertEqual(both.returncode, 1)
            self.assertIn("pass --graph or --manifest", both.stderr)
            neither = run(["properties", str(table_path)], env={**os.environ, "TAXONTOY_GRAPH": "", "TAXONTOY_MANIFEST": ""})
            self.assertIn("pass --graph or --manifest", neither.stderr)
            bad_graph = write_json(tmp, "bad-graph.json", {"nodes": []})
            bad = run(["properties", str(table_path), "--graph", str(bad_graph)], env=env)
            self.assertIn("edges array", bad.stderr)
            broken = minimal_table()
            del broken["excluded"]
            broken_path = write_json(tmp, "broken.json", broken)
            rejected = run(["properties", str(broken_path), "--graph", str(graph), "--json"], env=env)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("missing key: excluded", rejected.stdout)
            self.assertIsNone(taxontoy._elsewhere({"audit": {}, "concepts": {}, "cases": {}, "repos": []}, "audit", "subtree"))
            self.assertEqual(
                taxontoy._elsewhere({"audit": {}, "concepts": {}, "cases": {}, "repos": {"subtree": "hierarchy"}}, "audit", "subtree"),
                "repos",
            )


class ReviewFixTests(unittest.TestCase):
    def test_manifest_symlinks_cannot_escape_the_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outside = base / "outside"
            outside.mkdir()
            (outside / "heads.json").write_text(
                '{"heads":{"ext/sub":{"optional":{"subtree_prefix":"sub"}}}}\n',
                encoding="utf-8",
            )
            mdir = base / "man"
            mdir.mkdir()
            (mdir / "inside").mkdir()
            (mdir / "sym_file.json").symlink_to(outside / "heads.json")
            (mdir / "sym_dir").symlink_to(outside, target_is_directory=True)
            manifest = _write_manifest(mdir, [
                _source("ext/hub", _SHA_A, "child-heads.json", "heads", "sym_file.json"),
            ])
            with self.assertRaises(ValueError) as file_link:
                taxontoy._manifest_rows(str(manifest))
            self.assertIn("stay under the manifest directory", str(file_link.exception))
            manifest = _write_manifest(mdir, [
                _source("ext/hub", _SHA_A, "other-heads.json", "heads", "sym_dir/heads.json"),
            ])
            with self.assertRaises(ValueError) as dir_link:
                taxontoy._manifest_rows(str(manifest))
            self.assertIn("stay under the manifest directory", str(dir_link.exception))
            manifest = _write_manifest(mdir, [
                _source("ext/hub", _SHA_A, "inside", "heads", "inside"),
            ])
            with self.assertRaises(ValueError) as directory:
                taxontoy._manifest_rows(str(manifest))
            self.assertIn("stay under the manifest directory", str(directory.exception))
            (mdir / "real.json").write_text("{}\n", encoding="utf-8")
            (mdir / "link.json").symlink_to("real.json")
            manifest = _write_manifest(mdir, [
                _source("ext/hub", _SHA_A, "child-heads.json", "heads", "link.json"),
            ])
            with self.assertRaises(ValueError) as inside:
                taxontoy._manifest_rows(str(manifest))
            self.assertIn("stay under the manifest directory", str(inside.exception))

    def test_paths_and_repository_names_compare_after_normalization(self):
        table = {
            "properties": {"relations": {
                "subtree": {"disjoint_with": [{"relation": "vendor", "since": "2026-11-21", "reason": "pair"}]},
                "narrower": {"disjoint_with": [{"relation": "similarTo", "since": "2026-11-15", "reason": "pair"}]},
            }},
        }
        dotted = [
            {"kind": "subtree", "source": "https://github.com/MyOrg/Aoi.git", "target": "c", "path": "./a/b"},
            {"kind": "vendor", "source": "myorg/aoi", "target": "c", "path": "a/b"},
        ]
        hits, _skipped = taxontoy.property_violations(table, dotted)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["pair"]["repository"], "myorg/aoi")
        self.assertEqual(hits[0]["pair"]["path"], "a/b")
        self.assertEqual(taxontoy._double_distribution(hits)["count"], 1)
        different = [
            {"kind": "subtree", "source": "hub", "target": "c", "path": "./a"},
            {"kind": "vendor", "source": "hub", "target": "c", "path": "./b"},
        ]
        empty = [
            {"kind": "subtree", "source": "hub", "target": "c", "path": ""},
            {"kind": "vendor", "source": "hub", "target": "c", "path": "/"},
        ]
        self.assertEqual(taxontoy.property_violations(table, different)[0], [])
        self.assertEqual(taxontoy.property_violations(table, empty)[0], [])
        other = [
            {"kind": "narrower", "source": "hub", "target": "c", "path": "pkg"},
            {"kind": "similarTo", "source": "hub", "target": "c", "path": "pkg/lib.py"},
        ]
        found, _skipped = taxontoy.property_violations(table, other)
        self.assertEqual(len(found), 1)
        self.assertEqual(taxontoy._double_distribution(found), {"count": 0, "pairs": []})
        self.assertEqual(
            taxontoy._github_repo("git@github.com:MyOrg/Name.git"),
            "myorg/name",
        )
        self.assertEqual(
            taxontoy._github_repo("https://github.com/MyOrg/Name"),
            "myorg/name",
        )
        self.assertIsNone(taxontoy._github_repo(None))
        self.assertIsNone(taxontoy._github_repo("up/lib/extra", exact=True))
        self.assertEqual(taxontoy._github_repo("owner//name"), "owner/name")
        self.assertEqual(
            taxontoy._workflow_pins("- uses: actions/checkout@" + _SHA_A + "\n  repository: up/lib\n"),
            [("actions/checkout", _SHA_A)],
        )
        bare = taxontoy._pin_edges({
            "commit": _SHA_A,
            "path": ".github/workflows/ci.yml",
            "repo": " ",
            "role": "workflow",
            "text": "- uses: actions/checkout@" + _SHA_A + "\n",
        })
        self.assertEqual(bare[0]["source"], " ")
        self.assertEqual(taxontoy._double_distribution([{
            "relations": ["subtree", "vendor"],
            "pair": {"target": "child"},
        }])["pairs"], [{"source": None, "target": "child"}])

    def test_workflow_lines_and_sorted_graph_are_exact(self):
        first = "- uses: actions/checkout@" + _SHA_A + "\n  repository: up/lib\n  ref: " + _SHA_B + "\n"
        self.assertEqual(taxontoy._workflow_pins(first), [
            ("actions/checkout", _SHA_A),
            ("up/lib", _SHA_B),
        ])
        quoted = '  - uses: "actions/checkout@' + ("A" * 40) + '"\n'
        self.assertEqual(taxontoy._workflow_pins(quoted), [("actions/checkout", "a" * 40)])
        direct = "\n".join([
            "jobs:",
            "      - uses: actions/checkout@" + _SHA_A,
            "        repository: up/lib",
            "        ref: " + _SHA_B,
        ])
        self.assertEqual(
            taxontoy._workflow_pins(direct),
            [("actions/checkout", _SHA_A), ("up/lib", _SHA_B)],
        )
        local = [{
            "commit": _SHA_A,
            "path": ".github/workflows/ci.yml",
            "repo": "hub/app",
            "role": "workflow",
            "text": "      - uses: ./.github/workflows/local.yml@" + _SHA_A + "\n",
        }]
        self.assertEqual(taxontoy.build_repo_graph(local)["edges"], [])
        late = {
            "commit": _SHA_A, "path": "child-heads.json", "repo": "hub/app", "role": "heads",
            "text": json.dumps({"heads": {
                "hub/z": {"optional": {"subtree_prefix": "z"}},
                "hub/a": {"optional": {"subtree_prefix": "a"}},
            }}),
        }
        built = taxontoy.build_repo_graph([late])
        dumped = [json.dumps(edge, sort_keys=True) for edge in built["edges"]]
        self.assertEqual(dumped, sorted(dumped))
        self.assertEqual(built["edges"][0]["target"], "hub/a")
        violations, _skipped = taxontoy.property_violations(
            {"properties": {"relations": {"subtree": {"disjoint_with": [
                {"relation": "vendor", "since": "2026-11-21", "reason": "pair"},
            ]}}}},
            [
                {"kind": "subtree", "source": "hub", "target": "c", "path": "z"},
                {"kind": "vendor", "source": "hub", "target": "u", "path": "z"},
                {"kind": "subtree", "source": "hub", "target": "c", "path": "a"},
                {"kind": "vendor", "source": "hub", "target": "u", "path": "a"},
            ],
        )
        rendered = [json.dumps(item, sort_keys=True) for item in violations]
        self.assertEqual(rendered, sorted(rendered))
        counted = taxontoy._double_distribution(list(reversed(violations)))
        self.assertEqual(
            [row["path"] for row in counted["pairs"]],
            ["a", "z"],
        )

    def test_one_repository_is_one_node_and_the_remaining_checks_hold(self):
        sha = "a" * 40
        rows = [
            {
                "commit": sha, "path": "child-heads.json", "repo": "MyOrg/Aoi", "role": "heads",
                "text": json.dumps({"heads": {
                    "MyOrg/Aoi": {"optional": {"subtree_prefix": "child"}},
                    "MyOrg/Kid": {"optional": {"subtree_prefix": "kid"}},
                }}),
            },
            {
                "commit": sha, "path": "vendor.lock.json", "repo": "MyOrg/Aoi", "role": "lock",
                "text": json.dumps({"schema": "vendor-lock/1", "files": [{
                    "repository": "https://github.com/MyOrg/Lib.git",
                    "source": "a.py",
                    "destination": "vendor/a.py",
                }]}),
            },
            {
                "commit": sha, "path": "aoi.py", "repo": "MyOrg/Aoi", "role": "siblings",
                "text": "SIBLINGS = {'child': ('kid',)}\n",
            },
            {
                "commit": sha, "path": ".github/workflows/ci.yml", "repo": "MyOrg/Aoi", "role": "workflow",
                "text": "- uses: actions/checkout@" + sha + "\n",
            },
        ]
        graph = taxontoy.build_repo_graph(rows)
        self.assertEqual(
            [node["id"] for node in graph["nodes"]],
            ["actions/checkout", "myorg/aoi", "myorg/kid", "myorg/lib"],
        )
        self.assertEqual(
            {(edge["kind"], edge["source"], edge["target"]) for edge in graph["edges"]},
            {
                ("subtree", "myorg/aoi", "myorg/aoi"),
                ("subtree", "myorg/aoi", "myorg/kid"),
                ("vendor", "myorg/aoi", "myorg/lib"),
                ("consumes", "myorg/aoi", "myorg/kid"),
                ("workflow-pin", "myorg/aoi", "actions/checkout"),
            },
        )
        self.assertEqual(
            [item["repo"] for item in graph["provenance"]],
            ["MyOrg/Aoi", "MyOrg/Aoi", "MyOrg/Aoi", "MyOrg/Aoi"],
        )
        reversed_rows = [
            {"commit": sha, "path": "z.yml", "repo": "z/repo", "role": "workflow", "text": ""},
            {"commit": sha, "path": "a.yml", "repo": "a/repo", "role": "workflow", "text": ""},
        ]
        self.assertEqual(
            [(item["repo"], item["path"]) for item in taxontoy.build_repo_graph(reversed_rows)["provenance"]],
            [("a/repo", "a.yml"), ("z/repo", "z.yml")],
        )
        self.assertEqual(taxontoy._repo_key("up/lib/extra"), "up/lib/extra")
        self.assertEqual(
            taxontoy._workflow_pins(
                "- uses: actions/checkout@" + sha + "\n  repository: up/lib/extra\n  ref: " + ("b" * 40) + "\n"
            ),
            [("actions/checkout", sha)],
        )
        table = {"properties": {"relations": {"subtree": {"disjoint_with": [
            {"relation": "vendor", "since": "2026-11-21", "reason": "pair"},
        ]}}}}
        one_path, _skipped = taxontoy.property_violations(table, [
            {"kind": "subtree", "source": "hub", "target": "c", "path": "pkg"},
            {"kind": "vendor", "source": "hub", "target": "c"},
        ])
        same, _skipped = taxontoy.property_violations(table, [
            {"kind": "subtree", "source": "hub", "target": "c"},
            {"kind": "vendor", "source": "hub", "target": "c"},
        ])
        different, _skipped = taxontoy.property_violations(table, [
            {"kind": "subtree", "source": "hub", "target": "c"},
            {"kind": "vendor", "source": "hub", "target": "other"},
        ])
        self.assertEqual(one_path, [])
        self.assertEqual(len(same), 1)
        self.assertEqual(same[0]["pair"], {"source": "hub", "target": "c"})
        self.assertEqual(different, [])


class RepoBranchTests(unittest.TestCase):
    def test_short_circuits_and_the_other_side_of_each_guard(self):
        self.assertEqual(taxontoy._profile_names(None), list(taxontoy.WAVES))
        self.assertIsNone(taxontoy._norm_path(None))
        self.assertIsNone(taxontoy._norm_path(1))
        self.assertIsNone(taxontoy._norm_path("/"))
        self.assertIsNone(taxontoy._github_repo("bad!/name"))
        self.assertEqual(taxontoy.property_violations(None, [])[0], [])
        self.assertEqual(taxontoy._double_distribution([{
            "relations": ["subtree", "vendor"],
            "pair": {"repository": "hub"},
        }]), {"count": 1, "pairs": [{"repository": "hub"}]})
        self.assertFalse(taxontoy._disjoint_hit({"source": " ", "target": "c"}, {"source": "p", "target": "c"}))
        self.assertFalse(taxontoy._disjoint_hit({"source": "p", "target": ""}, {"source": "p", "target": "c"}))
        self.assertFalse(taxontoy._disjoint_hit({"source": "a", "target": "c"}, {"source": "b", "target": "c"}))
        self.assertEqual(
            taxontoy._pair_record({"source": "a", "target": "b"}, {"source": "a", "target": "b"}),
            {"source": "a", "target": "b"},
        )
        self.assertEqual(
            taxontoy._pair_record(
                {"source": "a", "path": "pkg", "target": "b"},
                {"source": "c", "path": "pkg", "target": "d"},
            ),
            {"source": "a", "target": "b"},
        )
        self.assertEqual(
            taxontoy._pair_record(
                {"source": "a", "path": "pkg", "target": "b"},
                {"source": "a", "target": "b"},
            ),
            {"source": "a", "target": "b"},
        )
        self.assertIsNone(taxontoy._child_repo("child", [{"hub": "other", "prefix": "child", "child": "x"}], "hub"))
        self.assertEqual(
            taxontoy._lock_holder(
                {"repo": "other", "path": "vendor.lock.json"},
                [{"hub": "hub", "prefix": "child", "child": "hub/child"}],
            ),
            "other",
        )
        self.assertEqual(
            taxontoy._lock_holder(
                {"repo": "hub", "path": "left/vendor/vendor.lock.json"},
                [{"hub": "hub", "prefix": "left", "child": "hub/left"}],
            ),
            "hub/left",
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "list.json").write_text("[]\n", encoding="utf-8")
            with self.assertRaises(ValueError) as listed:
                taxontoy._read_edge_graph(tmp / "list.json")
            self.assertIn("edges array", str(listed.exception))
            row = {
                "path": "heads.json",
                "repo": "hub",
                "role": "heads",
                "text": json.dumps({"heads": {
                    "hub/body": [],
                    "hub/slash": {"optional": {"subtree_prefix": "a/"}},
                }}),
            }
            with self.assertRaises(ValueError) as body:
                taxontoy._prefix_rows([row])
            self.assertIn("each head must name a repository", str(body.exception))
            row["text"] = json.dumps({"heads": {"hub/slash": {"optional": {"subtree_prefix": "a/"}}}})
            with self.assertRaises(ValueError) as slash:
                taxontoy._prefix_rows([row])
            self.assertIn("one path segment", str(slash.exception))
            lock = {"path": "vendor.lock.json", "repo": "hub", "role": "lock", "commit": _SHA_A, "text": "[]"}
            with self.assertRaises(ValueError) as data:
                taxontoy._lock_edges(lock, [])
            self.assertIn("vendor-lock/1", str(data.exception))
            for files, message in (
                ([{"repository": "", "source": "lib.py", "destination": "vendor/lib.py"}], "repository"),
                ([{"repository": "up/lib", "source": " ", "destination": "vendor/lib.py"}], "source"),
                ([{"repository": "up/lib", "source": "lib.py", "destination": "vendor/lib.py", "commit": 1}], "commit"),
            ):
                lock["text"] = json.dumps({"schema": "vendor-lock/1", "files": files})
                with self.assertRaises(ValueError) as exc:
                    taxontoy._lock_edges(lock, [])
                self.assertIn(message, str(exc.exception))
            (tmp / "odd.py").write_text(
                "value: int = 1\nitem.attr: int = 1\na = b = 1\nOTHER['x'] = ('y',)\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as missing:
                taxontoy._siblings_table((tmp / "odd.py").read_text(encoding="utf-8"), "odd.py")
            self.assertIn("not found", str(missing.exception))
            with self.assertRaises(TypeError):
                taxontoy._siblings_table(None, "odd.py")
            with self.assertRaises(ValueError) as nested:
                taxontoy._siblings_table("data[0]['x'] = 1\n", "odd.py")
            self.assertIn("not found", str(nested.exception))
            with self.assertRaises(ValueError):
                taxontoy._literal(None, "odd.py")
            records = [{"hub": "hub", "prefix": "left", "child": "hub/left", "row": {}}]
            sibling = {
                "path": "aoi.py", "repo": "hub", "role": "siblings", "commit": _SHA_A,
                "text": "SIBLINGS = {'left': 'right'}\n",
            }
            with self.assertRaises(ValueError) as dirs:
                taxontoy._consume_edges(sibling, records)
            self.assertIn("list directories", str(dirs.exception))
            pins = taxontoy._workflow_pins(
                "\n".join([
                    "      - uses: actions/checkout@" + _SHA_A,
                    "        with:",
                    "          - nested",
                    "      - uses: actions/checkout@" + _SHA_A,
                ])
            )
            self.assertEqual(pins, [("actions/checkout", _SHA_A), ("actions/checkout", _SHA_A)])
            self.assertEqual(taxontoy._workflow_pins("- uses: actions/checkout@" + _SHA_B), [("actions/checkout", _SHA_B)])


class RepoMutationTests(unittest.TestCase):
    def test_repo_mutations_of_the_real_source_are_killed(self):
        source = (ROOT / "taxontoy.py").read_text(encoding="utf-8")
        mutations = [
            (
                'if item["hub"] == row["repo"] and (\n'
                '            row["path"] == f"{item[\'prefix\']}/vendor.lock.json" or row["path"].startswith(item["prefix"] + "/")\n'
                "        )",
                'if item["hub"] == row["repo"] and row["path"] == "vendor.lock.json"',
            ),
            (
                "return left == right or left.startswith(right + \"/\") or right.startswith(left + \"/\")",
                "return left == right",
            ),
            (
                "if sibling is None or sibling == child:\n                continue",
                "if sibling is None:\n                continue",
            ),
            (
                'key = (repo, pin, row["path"])\n        if key in seen:\n            continue\n        seen.add(key)',
                'key = (repo, pin, row["path"])\n        if False:\n            continue\n        seen.add(key)',
            ),
            (
                "        if key in seen:\n            continue\n        seen.add(key)\n        pairs.append(row)",
                "        if key in seen:\n            pass\n        seen.add(key)\n        pairs.append(row)",
            ),
            (
                r"""^\s*(?:-\s+)?uses:\s*['"]?([^\s'"]+?)@([0-9a-fA-F]{40})['"]?(?:\s|#|$)""",
                r"""^\s*uses:\s*['"]?([^\s'"]+?)@([0-9a-fA-F]{40})['"]?(?:\s|#|$)""",
            ),
            (
                "if not checkout:\n            continue",
                "if False:\n            continue",
            ),
            (
                'if key in seen or left_name == right_name:',
                "if key in seen:",
            ),
            (
                '"relations": list(key),',
                '"relations": [left_name, right_name],',
            ),
            (
                'if not manifest:\n                raise ValueError("pass --manifest or set TAXONTOY_MANIFEST")',
                'if False:\n                raise ValueError("pass --manifest or set TAXONTOY_MANIFEST")',
            ),
            (
                "if violations and args.strict:\n                return 2",
                "if violations and False:\n                return 2",
            ),
            (
                'ident = f"repository {pair[\'repository\']} path {pair[\'path\']}"',
                'ident = "repository"',
            ),
            (
                'if "prefix" in pair:\n                row["prefix"] = pair["prefix"]',
                'if False:\n                row["prefix"] = pair["prefix"]',
            ),
            (
                "if not full.is_relative_to(root) or not full.is_file():",
                "if not full.is_file():",
            ),
            (
                'if part == ".":\n            continue',
                "if False:\n            continue",
            ),
            (
                'if "path" in left or "path" in right:',
                "if False:",
            ),
            (
                "edges.sort(key=lambda edge: json.dumps(edge, ensure_ascii=False, sort_keys=True))",
                "edges = list(edges)",
            ),
            (
                'if repo is None:\n            continue',
                "if False:\n            continue",
            ),
            (
                'if item["relations"] != ["subtree", "vendor"]:\n            continue',
                "if False:\n            continue",
            ),
            (
                'lines.append(f"nodes {len(payload[\'nodes\'])}")',
                'lines.append("nodes 0")',
            ),
            (
                '"ok": True,\n                "provenance": built["provenance"],',
                '"ok": False,\n                "provenance": built["provenance"],',
            ),
            (
                "violations.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))",
                "violations = list(violations)",
            ),
            (
                "pairs.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))",
                "pairs = list(pairs)",
            ),
            (
                '    provenance.sort(key=lambda item: (item["repo"], item["path"], item["role"]))',
                "    provenance = list(provenance)",
            ),
            (
                "        if candidate.is_symlink():",
                "        if False:",
            ),
            (
                "    return _github_repo(value, exact=True) or value.casefold()",
                "    return _github_repo(value) or value.casefold()",
            ),
            (
                "        canon = _github_repo(repository, exact=True) if repository and ref else None",
                "        canon = _github_repo(repository) if repository and ref else None",
            ),
            (
                '    if "path" in left or "path" in right:',
                '    if "path" in left and "path" in right:',
            ),
            (
                '        and _repo_key(left.get("target")) == _repo_key(right.get("target"))',
                '        and _repo_key(left.get("target")) != _repo_key(right.get("target"))',
            ),
        ]
        manifest = ROOT / "fixtures" / "repos" / "manifest.json"
        table = ROOT / "otl-relations.json"
        graph = ROOT / "fixtures" / "repos" / "graph.json"
        golden = _repo_snapshot(taxontoy, manifest, table, graph)
        self.assertEqual(golden, _repo_snapshot(taxontoy, manifest, table, graph))
        killed, survived = [], []
        for old, new in mutations:
            self.assertEqual(source.count(old), 1, old)
            try:
                module = _load_mutant(source.replace(old, new, 1))
                found = _repo_snapshot(module, manifest, table, graph)
            except Exception:
                killed.append(old)
                continue
            if found != golden:
                killed.append(old)
            else:
                survived.append(old)
        sys.stderr.write(f"repo mutation killed={len(killed)} survived={len(survived)}\n")
        self.assertEqual(survived, [], survived)


def _repo_snapshot(module, manifest, table, graph):
    import io
    from contextlib import redirect_stderr, redirect_stdout

    built, _inputs = module._graph_from_manifest(str(manifest))
    edges = built["edges"]
    synthetic = [
        {"kind": "subtree", "source": "hub", "target": "child", "path": "pkg"},
        {"kind": "vendor", "source": "hub", "target": "up", "path": "pkg/lib.py"},
        {"kind": "vendor", "source": "hub", "target": "child"},
        {"kind": "narrower", "source": "p", "target": "c"},
        {"kind": "similarTo", "source": "p", "target": "c"},
    ]
    catalog = json.loads(Path(table).read_text(encoding="utf-8"))
    one_way = json.loads(Path(table).read_text(encoding="utf-8"))
    one_way["properties"]["relations"]["vendor"]["disjoint_with"] = [{
        "relation": "subtree", "since": "2026-11-21", "reason": "one way",
    }]
    del one_way["properties"]["relations"]["subtree"]["disjoint_with"]
    self_table = {"properties": {"relations": {"subtree": {"disjoint_with": [
        {"relation": "subtree", "since": "2026-11-21", "reason": "self"},
    ]}}}}
    violations, skipped = module.property_violations(catalog, synthetic)
    ordered, _skipped = module.property_violations(one_way, synthetic)
    self_hits, _skipped = module.property_violations(self_table, synthetic)
    with tempfile.TemporaryDirectory(prefix="taxontoy-repo-") as directory:
        sample = Path(directory) / "sample.json"
        sample.write_text(json.dumps({"edges": synthetic[:2]}), encoding="utf-8")
        out = io.StringIO()
        err = io.StringIO()
        graph_out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = module.main([
                "properties", str(table), "--graph", str(sample), "--format", "text", "--strict",
            ])
            try:
                missing = module.main(["graph"])
            except SystemExit as exc:
                missing = exc.code if isinstance(exc.code, int) else 1
        graph_text = io.StringIO()
        with redirect_stdout(graph_out), redirect_stderr(io.StringIO()):
            module.main(["graph", "--manifest", str(manifest), "--format", "json"])
        with redirect_stdout(graph_text), redirect_stderr(io.StringIO()):
            module.main(["graph", "--manifest", str(manifest), "--format", "text"])
        text = out.getvalue().replace(str(directory), "TMP")
        nodes_line = next(line for line in graph_text.getvalue().splitlines() if line.startswith("nodes "))
    escape = "read"
    with tempfile.TemporaryDirectory() as base_name:
        base = Path(base_name)
        outside = base / "outside"
        outside.mkdir()
        (outside / "heads.json").write_text('{"heads":{}}\n', encoding="utf-8")
        mdir = base / "man"
        mdir.mkdir()
        (mdir / "sym_dir").symlink_to(outside, target_is_directory=True)
        (mdir / "manifest.json").write_text(json.dumps({"sources": [{
            "commit": "a" * 40, "file": "sym_dir/heads.json", "path": "child-heads.json",
            "repo": "ext/hub", "role": "heads",
        }]}), encoding="utf-8")
        try:
            module._graph_from_manifest(str(mdir / "manifest.json"))
        except ValueError as exc:
            escape = "stay" if "stay under" in str(exc) else str(exc)
        (mdir / "real.json").write_text("{}\n", encoding="utf-8")
        (mdir / "link.json").symlink_to("real.json")
        (mdir / "manifest.json").write_text(json.dumps({"sources": [{
            "commit": "a" * 40, "file": "link.json", "path": "child-heads.json",
            "repo": "ext/hub", "role": "heads",
        }]}), encoding="utf-8")
        inside = "read"
        try:
            module._manifest_rows(str(mdir / "manifest.json"))
        except ValueError as exc:
            inside = "stay" if "stay under" in str(exc) else str(exc)
    ordered_hits, _skipped = module.property_violations(
        {"properties": {"relations": {"subtree": {"disjoint_with": [
            {"relation": "vendor", "since": "2026-11-21", "reason": "pair"},
        ]}}}},
        [
            {"kind": "subtree", "source": "hub", "target": "c", "path": "z"},
            {"kind": "vendor", "source": "hub", "target": "u", "path": "z"},
            {"kind": "subtree", "source": "hub", "target": "c", "path": "a"},
            {"kind": "vendor", "source": "hub", "target": "u", "path": "a"},
        ],
    )
    dotted, _skipped = module.property_violations(catalog, [
        {"kind": "subtree", "source": "hub", "target": "c", "path": "./a/b"},
        {"kind": "vendor", "source": "hub", "target": "c", "path": "a/b"},
        {"kind": "subtree", "source": "hub", "target": "c", "path": "./a"},
        {"kind": "vendor", "source": "hub", "target": "c", "path": "./b"},
    ])
    try:
        local = module.build_repo_graph([{
            "commit": "a" * 40,
            "path": ".github/workflows/ci.yml",
            "repo": "hub/app",
            "role": "workflow",
            "text": "      - uses: ./.github/workflows/local.yml@" + ("a" * 40) + "\n",
        }])
        local_edges = len(local["edges"])
    except Exception as exc:
        local_edges = type(exc).__name__
    return {
        "checkout_only": module._workflow_pins(
            "- run: echo\n  repository: up/lib\n  ref: " + ("d" * 40) + "\n"
            + "- uses: actions/checkout@" + ("b" * 40) + "\n  repository: up/lib\n  ref: " + ("c" * 40) + "\n"
        ),
        "code": code,
        "counts": module._relation_counts(edges),
        "escape": escape,
        "hit_paths": [item["pair"]["path"] for item in ordered_hits],
        "nodes_line": nodes_line,
        "pair_paths": [
            row["path"] for row in module._double_distribution(list(reversed(ordered_hits)))["pairs"]
        ],
        "diff_ends": len(module.property_violations(catalog, [
            {"kind": "subtree", "source": "hub", "target": "c"},
            {"kind": "vendor", "source": "hub", "target": "other"},
        ])[0]),
        "dotted": len(dotted),
        "double": module._double_distribution(violations),
        "extra": module._repo_key("up/lib/extra"),
        "dup": module._double_distribution(violations + violations),
        "graph_ok": json.loads(graph_out.getvalue())["ok"],
        "holders": sorted({edge["source"] for edge in edges if edge["kind"] == "vendor"}),
        "inside": inside,
        "local": local_edges,
        "missing": missing,
        "one_path": len(module.property_violations(catalog, [
            {"kind": "subtree", "source": "hub", "target": "c", "path": "pkg"},
            {"kind": "vendor", "source": "hub", "target": "c"},
        ])[0]),
        "norm": module._norm_path("./a/b"),
        "ordered": [item["relations"] for item in ordered],
        "provenance": [
            (item["repo"], item["path"])
            for item in module.build_repo_graph([
                {"commit": "a" * 40, "path": "z.yml", "repo": "z/repo", "role": "workflow", "text": ""},
                {"commit": "a" * 40, "path": "a.yml", "repo": "a/repo", "role": "workflow", "text": ""},
            ])["provenance"]
        ],
        "same_ends": len(module.property_violations(catalog, [
            {"kind": "subtree", "source": "hub", "target": "c"},
            {"kind": "vendor", "source": "hub", "target": "c"},
        ])[0]),
        "three": module._workflow_pins(
            "- uses: actions/checkout@" + ("b" * 40) + "\n  repository: up/lib/extra\n  ref: " + ("c" * 40) + "\n"
        ),
        "other_double": module._double_distribution([{
            "relations": ["narrower", "similarTo"],
            "pair": {"path": "pkg", "repository": "hub"},
        }])["count"],
        "self": len(self_hits),
        "skipped": skipped,
        "sorted_edges": edges == sorted(
            edges, key=lambda edge: json.dumps(edge, ensure_ascii=False, sort_keys=True),
        ),
        "stderr": err.getvalue(),
        "text": text,
        "violations": violations,
    }


def _exclusion(relation, kind="out_of_scope", alias_of=None, since="2026-11-21", reason="recorded"):
    row = {"relation": relation, "kind": kind, "since": since, "reason": reason}
    if alias_of is not None:
        row["alias_of"] = alias_of
    return row


def _v3_table():
    table = minimal_table()
    table["schema"] = taxontoy.SCHEMA_ID
    table["excluded"] = {
        "audit": [
            _exclusion("belongsTo"),
            _exclusion("blocks"),
            _exclusion("depends_on", "alias", "dependsOn"),
            _exclusion("observes"),
            _exclusion("overlapsWith", "alias", "duplicates"),
        ],
        "concepts": [_exclusion("hasName")],
        "cases": [],
    }
    table["properties"]["relations"]["belongsTo"] = {
        "domain": {"types": ["File", "PullRequest"], "since": "2026-11-21", "reason": "sources seen"},
        "range": {"types": ["Repository"], "since": "2026-11-21", "reason": "one target"},
    }
    return table


def _sha(char):
    return char * 40


class RelationV3Tests(unittest.TestCase):
    def test_version_2_commands_keep_their_shape(self):
        table = minimal_table()
        self.assertEqual(table["schema"], taxontoy.SCHEMA_V2)
        self.assertEqual(taxontoy.validate_document(table), [])
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(tmp, "table.json", table)
            checked = run(["validate", str(path), "--format", "json"])
        self.assertEqual(checked.returncode, 0, checked.stderr)
        payload = json.loads(checked.stdout)
        self.assertEqual(payload["excluded"]["audit"], ["blocks", "depends_on", "observes", "overlapsWith"])
        graphs = {wave: body["edges"] for wave, body in fixture_graphs().items()}
        waves, errors = taxontoy.cover_waves(table, graphs, taxontoy._load_knowledgegraph(ONTO))
        self.assertEqual(errors, [])
        for body in waves.values():
            self.assertNotIn("edge_weight", body)
            self.assertNotIn("types", body)
        found, _notes = taxontoy.property_candidates(table, {"dependsOn": [{"source": "a", "target": "b"}], "depends_on": [{"source": "a", "target": "b"}]}, {})
        separator = next(item for item in found if item["detection"] == "separator")
        self.assertFalse(separator["declared"])
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(tmp, "table.json", table)
            text = run(["validate", str(path), "--format", "text"])
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn("excluded audit depends_on", text.stdout)
        self.assertNotIn("alias_of", text.stdout)

    def test_version_2_table_still_reads_after_the_checked_in_table_moved_to_v3(self):
        data = json.loads(TABLE.read_text(encoding="utf-8"))
        data["schema"] = taxontoy.SCHEMA_V2
        for wave, entries in list(data["excluded"].items()):
            data["excluded"][wave] = [item["relation"] for item in entries] if entries and isinstance(entries[0], dict) else list(entries)
        data["properties"]["relations"].pop("belongsTo")
        self.assertEqual(taxontoy.validate_document(data), [])
        ranked = taxontoy.rank_unclassified(data, [
            {"relation": "depends_on", "source": "a", "target": "b"},
            {"relation": "dependsOn", "source": "a", "target": "b"},
        ], "audit")
        self.assertEqual(ranked["ranking"][0]["relation"], "depends_on")
        self.assertEqual(ranked["ranking"][0]["status"], "excluded")

    def test_exclusion_objects_require_kind_reason_and_alias_target(self):
        def errors_of(entries):
            table = _v3_table()
            table["excluded"]["audit"] = entries
            return taxontoy.validate_document(table)

        self.assertEqual(taxontoy.validate_document(_v3_table()), [])
        self.assertIn("excluded.audit[0]: must be an object", errors_of(["belongsTo"]))
        self.assertIn("excluded.audit[0]: unknown field note", errors_of([
            {**_exclusion("belongsTo"), "note": "no"},
        ]))
        self.assertIn("excluded.audit[0]: missing relation", errors_of([_exclusion("  ")]))
        self.assertIn("audit: dependsOn is both classified and excluded", errors_of([
            _exclusion("dependsOn"),
        ]))
        self.assertIn("excluded.audit[0]: kind must be out_of_scope or alias", errors_of([
            _exclusion("belongsTo", kind="inverse"),
        ]))
        self.assertIn("excluded.audit[0]: missing since", errors_of([
            {"relation": "belongsTo", "kind": "out_of_scope", "reason": "why"},
        ]))
        self.assertIn("excluded.audit[0]: invalid since", errors_of([
            _exclusion("belongsTo", since="2026-02-31"),
        ]))
        self.assertIn("excluded.audit[0]: missing reason", errors_of([
            _exclusion("belongsTo", reason="  "),
        ]))
        self.assertIn("excluded.audit[0]: missing alias_of", errors_of([
            _exclusion("depends_on", "alias"),
        ]))
        self.assertIn("excluded.audit[0]: alias_of itself", errors_of([
            _exclusion("depends_on", "alias", "depends_on"),
        ]))
        self.assertIn("excluded.audit[0]: alias_of unknown relation ghost", errors_of([
            _exclusion("depends_on", "alias", "ghost"),
        ]))
        self.assertIn("excluded.audit[0]: alias_of is classified in concepts", errors_of([
            _exclusion("depends_on", "alias", "narrower"),
        ]))
        self.assertIn("excluded.audit[0]: alias_of is only for alias", errors_of([
            {**_exclusion("belongsTo"), "alias_of": "dependsOn"},
        ]))
        self.assertIn("excluded.audit: must be sorted and unique", errors_of([
            _exclusion("depends_on", "alias", "dependsOn"),
            _exclusion("belongsTo"),
        ]))
        self.assertIn("excluded.audit: must be sorted and unique", errors_of([
            _exclusion("belongsTo"),
            _exclusion("belongsTo"),
        ]))
        mixed = minimal_table()
        mixed["excluded"]["audit"] = ["blocks", {"relation": "depends_on"}]
        self.assertIn("excluded.audit: must be sorted and unique", taxontoy.validate_document(mixed))
        self.assertIn("excluded.audit: names must be non-empty strings", taxontoy.validate_document(mixed))

    def test_edge_weight_is_classified_edges_over_every_edge(self):
        table = _v3_table()
        graphs = {wave: list(body["edges"]) for wave, body in fixture_graphs().items()}
        graphs["audit"].append("not-an-edge")
        waves, errors = taxontoy.cover_waves(table, graphs, taxontoy._load_knowledgegraph(ONTO), {})
        self.assertTrue(any(error.startswith("malformed audit:") for error in errors))
        weight = waves["audit"]["edge_weight"]
        self.assertEqual(weight["classified"], 2)
        self.assertEqual(weight["excluded"], 4)
        self.assertEqual(weight["unclassified"], 0)
        self.assertEqual(weight["total"], 7)
        self.assertEqual(weight["classified_ratio"], {"denominator": 7, "numerator": 2})
        self.assertNotIn("types", waves["concepts"])
        skipped = taxontoy._type_gaps(table, {}, {}, table["concepts"], set(), set())
        self.assertIsNone(skipped)

    def test_domain_and_range_are_optional_and_checked(self):
        def type_errors(body):
            table = _v3_table()
            table["properties"]["relations"]["belongsTo"] = body
            return taxontoy.validate_document(table)

        self.assertIn(
            "properties.relations.belongsTo.domain: must be an object",
            type_errors({"domain": ["File"]}),
        )
        self.assertIn(
            "properties.relations.belongsTo.domain: unknown field note",
            type_errors({"domain": {"types": ["File"], "since": "2026-11-21", "reason": "why", "note": 1}}),
        )
        self.assertIn(
            "properties.relations.belongsTo.domain: types must be a non-empty array",
            type_errors({"domain": {"types": [], "since": "2026-11-21", "reason": "why"}}),
        )
        self.assertIn(
            "properties.relations.belongsTo.domain: types must be sorted and unique",
            type_errors({"domain": {"types": ["PullRequest", "File"], "since": "2026-11-21", "reason": "why"}}),
        )
        self.assertIn(
            "properties.relations.belongsTo.domain: types must be sorted and unique",
            type_errors({"domain": {"types": [1], "since": "2026-11-21", "reason": "why"}}),
        )
        self.assertIn(
            "properties.relations.belongsTo.domain: types must be non-empty strings",
            type_errors({"domain": {"types": [" "], "since": "2026-11-21", "reason": "why"}}),
        )
        self.assertIn(
            "properties.relations.belongsTo.range: missing since",
            type_errors({"range": {"types": ["Repository"], "reason": "why"}}),
        )
        table = _v3_table()
        table["properties"]["relations"]["dependsOn"] = {
            "domain": {"types": ["PullRequest"], "since": "2026-11-21", "reason": "one source"},
        }
        edges = [
            {"relation": "belongsTo", "source": "file", "target": "repo"},
            {"relation": "belongsTo", "source": "issue", "target": "repo"},
            {"relation": "belongsTo", "source": "file", "target": "other"},
            {"relation": "belongsTo", "source": "loose", "target": "repo"},
            {"relation": "dependsOn", "source": "pr", "target": "pr"},
            {"relation": "dependsOn", "source": "issue", "target": "pr"},
            "not-an-edge",
        ]
        nodes = {
            "file": "File",
            "issue": "Issue",
            "repo": "Repository",
            "other": "Commit",
            "pr": "PullRequest",
        }
        violations, skipped = taxontoy.property_violations(table, edges, nodes)
        self.assertEqual(skipped, 1)
        kinds = {(item["property"], item["relations"][0], item["pair"]["source_type"], item["pair"]["target_type"]) for item in violations}
        self.assertIn(("domain", "belongsTo", "Issue", "Repository"), kinds)
        self.assertIn(("range", "belongsTo", "File", "Commit"), kinds)
        self.assertIn(("domain", "belongsTo", None, "Repository"), kinds)
        self.assertIn(("domain", "dependsOn", "Issue", "PullRequest"), kinds)
        self.assertNotIn(("domain", "dependsOn", "PullRequest", "PullRequest"), kinds)
        self.assertEqual(taxontoy.property_violations(table, edges, {})[0], [])
        gaps = taxontoy._type_gaps(
            table,
            {"belongsTo": [edge for edge in edges if isinstance(edge, dict) and edge.get("relation") == "belongsTo"],
             "dependsOn": [edge for edge in edges if isinstance(edge, dict) and edge.get("relation") == "dependsOn"]},
            nodes,
            table["audit"],
            {"belongsTo", "depends_on"},
            {"belongsTo", "dependsOn"},
        )
        belongs = gaps["relations"]["belongsTo"]
        self.assertEqual(belongs["gap"]["actual_only"], [
            {"source": "File", "target": "Commit"},
            {"source": "Issue", "target": "Repository"},
        ])
        self.assertIn({"source": "PullRequest", "target": "Repository"}, belongs["gap"]["declared_only"])
        depends = gaps["relations"]["dependsOn"]
        self.assertEqual(depends["gap"]["actual_only"], [{"source": "Issue", "target": "PullRequest"}])
        range_only = _v3_table()
        range_only["properties"]["relations"]["belongsTo"] = {
            "range": {"types": ["Repository"], "since": "2026-11-21", "reason": "target only"},
        }
        range_gaps = taxontoy._type_gaps(
            range_only,
            {"belongsTo": [{"source": "file", "target": "other"}]},
            nodes,
            range_only["audit"],
            {"belongsTo"},
            {"belongsTo"},
        )
        self.assertEqual(
            range_gaps["relations"]["belongsTo"]["gap"]["actual_only"],
            [{"source": "File", "target": "Commit"}],
        )
        empty_domain = taxontoy._declared_pairs({"domain": ["File"]}, set())
        self.assertEqual(empty_domain, {("File", None)})
        quiet = taxontoy._type_gaps(table, {}, {}, table["audit"], {"belongsTo"}, set())
        self.assertEqual(quiet["note"], "types: skipped; nodes have no type")
        self.assertNotIn("gap", quiet["relations"]["belongsTo"])
        self.assertEqual(taxontoy._type_declarations({"properties": []}), {})
        self.assertEqual(taxontoy._type_declarations({"properties": {"relations": {"belongsTo": "nope"}}}), {})
        self.assertEqual(
            taxontoy._type_declarations({"properties": {"relations": {"belongsTo": {"domain": "File"}}}}),
            {},
        )
        self.assertFalse(taxontoy._pair_inside({"domain": ["File"], "range": ["Repository"]}, None, "Repository"))
        self.assertFalse(taxontoy._pair_inside({"domain": ["File"]}, "Issue", "Repository"))
        self.assertTrue(taxontoy._pair_inside({"domain": ["File"]}, "File", "Repository"))

    def test_coverage_text_shows_weight_and_type_gaps(self):
        table = _v3_table()
        graphs = fixture_graphs()
        graphs["audit"]["nodes"] = [
            {"id": "a", "type": "PullRequest"},
            {"id": "b", "type": "PullRequest"},
            {"id": "c", "type": "PullRequest"},
            {"id": "x", "type": "Observation"},
            {"id": "y", "type": "PullRequest"},
            {"id": "repo", "type": "Repository"},
            {"id": "issue", "type": "Issue"},
        ]
        graphs["audit"]["edges"].append({"relation": "belongsTo", "source": "issue", "target": "repo"})
        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(tmp, "table.json", table)
            args = ["coverage", str(path), "--onto", ONTO, "--format", "text"]
            for wave, payload in graphs.items():
                graph = write_json(tmp, f"{wave}.json", payload)
                args.extend(["--graph", f"{wave}={graph}"])
            result = run(args)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("edges classified 2 excluded 5 unclassified 0 total 7 ratio 2/7", result.stdout)
            self.assertIn("types belongsTo domain File PullRequest range Repository", result.stdout)
            self.assertIn("actual belongsTo Issue->Repository:1", result.stdout)
            self.assertIn("gap actual_only belongsTo Issue->Repository", result.stdout)
            self.assertIn("gap declared_only belongsTo File->Repository", result.stdout)
            checked = run(["validate", str(path), "--format", "text"])
            self.assertIn("excluded audit depends_on alias alias_of dependsOn since 2026-11-21", checked.stdout)
            bare = fixture_graphs()
            bare_args = ["coverage", str(path), "--onto", ONTO, "--format", "text"]
            for wave, payload in bare.items():
                graph = write_json(tmp, f"bare-{wave}.json", payload)
                bare_args.extend(["--graph", f"{wave}={graph}"])
            noted = run(bare_args)
            self.assertIn("note types: skipped; nodes have no type", noted.stdout)

    def test_properties_text_reports_a_type_violation(self):
        table = _v3_table()
        graph = {
            "nodes": [
                {"id": "issue", "type": "Issue"},
                {"id": "repo", "type": "Repository"},
            ],
            "edges": [{"relation": "belongsTo", "source": "issue", "target": "repo"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            table_path = write_json(tmp, "table.json", table)
            graph_path = write_json(tmp, "graph.json", graph)
            result = run([
                "properties", str(table_path), "--graph", str(graph_path), "--format", "text", "--strict",
            ])
        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertIn("violation domain belongsTo source_type Issue target_type Repository", result.stdout)
        self.assertIn("edge belongsTo issue repo", result.stdout)

    def test_alias_declaration_marks_same_pairs_and_not_an_inverse(self):
        table = _v3_table()
        found, _notes = taxontoy.property_candidates(table, {
            "dependsOn": [{"source": "a", "target": "b"}],
            "depends_on": [{"source": "a", "target": "b"}],
            "DependsOn": [{"source": "a", "target": "b"}],
            "depends-on": [{"source": "a", "target": "b"}],
            "blocks": [{"source": "b", "target": "a"}],
            "duplicates": [{"source": "a", "target": "c"}],
            "overlapsWith": [{"source": "a", "target": "c"}],
        }, {})
        declared = {
            (item["detection"], tuple(item["relations"])): item["declared"]
            for item in found
        }
        self.assertTrue(declared[("separator", ("DependsOn", "depends-on", "dependsOn", "depends_on"))])
        self.assertTrue(declared[("same-pairs", ("dependsOn", "depends_on"))])
        self.assertTrue(declared[("same-pairs", ("duplicates", "overlapsWith"))])
        self.assertFalse(declared[("inverse-pair", ("blocks", "depends_on"))])
        self.assertIsNone(taxontoy._alias_target(minimal_table(), "depends_on"))
        self.assertEqual(taxontoy._alias_target(table, "depends_on"), "dependsOn")
        self.assertIsNone(taxontoy._alias_target({"schema": taxontoy.SCHEMA_ID, "excluded": []}, "depends_on"))
        self.assertIsNone(taxontoy._alias_target({"schema": taxontoy.SCHEMA_ID, "excluded": {"audit": "nope"}}, "depends_on"))

    def test_reconcile_partitions_workflow_pins(self):
        table = minimal_table()
        table["repos"] = {"workflow-pin": "dependency"}
        table["direction"]["workflow-pin"] = "forward"
        table["excluded"]["repos"] = []
        checkout = "Example/App"
        same = _sha("a")
        too_only = _sha("b")
        audit_only = _sha("c")
        left = _sha("d")
        right = _sha("e")
        edges = [
            {"kind": "workflow-pin", "source": checkout, "path": ".github/workflows/ci.yml", "target": "actions/checkout", "pin": same.upper()},
            {"kind": "workflow-pin", "source": checkout, "path": ".github/workflows/ci.yml", "target": "actions/setup-node", "pin": too_only},
            {"kind": "workflow-pin", "source": checkout, "path": ".github/workflows/pages.yml", "target": "actions/checkout", "pin": left},
            {"kind": "workflow-pin", "source": "other/app", "path": ".github/workflows/ci.yml", "target": "actions/checkout", "pin": same},
            {"kind": "vendor", "source": checkout, "target": "up/lib"},
            {"kind": "workflow-pin", "source": checkout, "path": "bad.yml", "target": "actions/checkout", "pin": "short"},
            {"kind": "workflow-pin", "source": "", "path": "bad.yml", "target": "actions/checkout", "pin": same},
            "not-an-edge",
        ]
        audit = {
            "schema": "gh-identity-pin-audit/1",
            "results": [
                {
                    "repository": "Actions/Checkout",
                    "commit": same,
                    "state": "ok",
                    "pins": [{
                        "repository": "Actions/Checkout",
                        "commit": same,
                        "file": ".github/workflows/ci.yml",
                        "line": 3,
                    }],
                },
                {
                    "repository": "actions/upload-artifact",
                    "commit": audit_only.upper(),
                    "pins": [{"file": ".github/workflows/ci.yml", "line": 9}],
                },
                {
                    "repository": "actions/checkout",
                    "commit": right,
                    "pins": [{"file": ".github/workflows/pages.yml", "line": 4, "source": "  "}],
                },
                {
                    "repository": None,
                    "commit": None,
                    "state": "unavailable",
                    "pins": [{"file": ".github/workflows/secret.yml", "line": 1}],
                },
            ],
        }
        too_pins, too_skipped = taxontoy._workflow_pin_records(table, edges)
        self.assertEqual(too_skipped, 3)
        audit_pins, audit_skipped = taxontoy._read_pin_audit_from(audit, "example/app")
        self.assertEqual(audit_skipped, 1)
        diff = taxontoy.reconcile_pins(too_pins, audit_pins)
        self.assertEqual(diff["same"], [{
            "path": ".github/workflows/ci.yml",
            "sha": same,
            "source": "example/app",
            "target": "actions/checkout",
        }])
        self.assertEqual([row["sha"] for row in diff["only_taxontoy"]], [too_only, same])
        self.assertEqual(diff["only_taxontoy"][0]["target"], "actions/setup-node")
        self.assertEqual(diff["only_taxontoy"][1]["source"], "other/app")
        self.assertEqual(diff["only_pin_audit"], [{
            "path": ".github/workflows/ci.yml",
            "sha": audit_only,
            "source": "example/app",
            "target": "actions/upload-artifact",
        }])
        self.assertEqual(diff["different_sha"], [{
            "path": ".github/workflows/pages.yml",
            "pin_audit": [right],
            "source": "example/app",
            "target": "actions/checkout",
            "taxontoy": [left],
        }])
        self.assertEqual(taxontoy.reconcile_pins([], [])["same"], [])
        with self.assertRaises(ValueError):
            taxontoy._workflow_pin_records(minimal_table(), [])

    def test_reconcile_cli_is_deterministic_and_records_provenance(self):
        table = minimal_table()
        table["repos"] = {"workflow-pin": "dependency"}
        table["direction"]["workflow-pin"] = "forward"
        table["excluded"]["repos"] = []
        sha = _sha("a")
        other = _sha("b")
        graph = {"edges": [
            {"kind": "workflow-pin", "source": "example/app", "path": ".github/workflows/ci.yml",
             "target": "actions/checkout", "pin": sha},
            {"kind": "workflow-pin", "source": "example/app", "path": ".github/workflows/ci.yml",
             "target": "actions/setup-node", "pin": other},
        ]}
        audit = {
            "schema": "gh-identity-pin-audit/1",
            "results": [{
                "repository": "actions/checkout",
                "commit": sha,
                "pins": [{"repository": "actions/checkout", "commit": sha, "file": ".github/workflows/ci.yml"}],
            }, {
                "repository": "actions/setup-node",
                "commit": _sha("c"),
                "pins": [{"commit": _sha("c"), "file": ".github/workflows/ci.yml", "source": "example/app"}],
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            table_path = write_json(tmp, "table.json", table)
            graph_path = write_json(tmp, "graph.json", graph)
            audit_path = write_json(tmp, "pin-audit.json", audit)
            args = [
                "reconcile", str(table_path), "--graph", str(graph_path),
                "--pin-audit", str(audit_path), "--repo", " Example/App ",
            ]
            first = run([*args, "--format", "json"])
            second = run([*args, "--json"])
            self.assertEqual(first.returncode, 1, first.stderr + first.stdout)
            self.assertEqual(first.stdout, second.stdout)
            payload = json.loads(first.stdout)
            self.assertEqual(
                first.stdout,
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            self.assertEqual(payload["version"], taxontoy.__version__)
            self.assertEqual(payload["repository"], "example/app")
            self.assertEqual(payload["counts"]["same"], 1)
            self.assertEqual(payload["counts"]["different_sha"], 1)
            self.assertEqual(payload["counts"]["only_taxontoy"], 0)
            self.assertEqual(payload["counts"]["only_pin_audit"], 0)
            self.assertEqual({item["path"] for item in payload["inputs"]}, {
                str(table_path), str(graph_path), str(audit_path),
            })
            text = run([*args, "--format", "text"])
            self.assertEqual(text.returncode, 1, text.stderr)
            self.assertIn("different_sha example/app .github/workflows/ci.yml actions/setup-node", text.stdout)
            self.assertIn(f"taxontoy {other} pin_audit {_sha('c')}", text.stdout)
            self.assertIn("same example/app .github/workflows/ci.yml actions/checkout", text.stdout)
            self.assertIn("repository example/app", text.stdout)
            strict = run([*args, "--strict", "--json"])
            self.assertEqual(strict.returncode, 2)
            conflict = run([*args, "--json", "--format", "text"])
            self.assertEqual(conflict.returncode, 1)
            self.assertIn("disagree", conflict.stderr)
            out = Path(tmp) / "out.json"
            written = run([*args, "--json", "--output", str(out)])
            self.assertEqual(written.stdout, "")
            self.assertEqual(out.read_text(encoding="utf-8"), first.stdout)
            env = os.environ.copy()
            env["TAXONTOY_TABLE"] = str(table_path)
            env["TAXONTOY_GRAPH"] = str(graph_path)
            env["TAXONTOY_PIN_AUDIT"] = str(audit_path)
            env["TAXONTOY_REPO"] = "example/app"
            from_env = run(["reconcile", "--json"], env=env)
            self.assertEqual(json.loads(from_env.stdout)["counts"], payload["counts"])
            missing_audit = run(["reconcile", str(table_path), "--graph", str(graph_path)], env=os.environ.copy())
            self.assertEqual(missing_audit.returncode, 1)
            self.assertIn("pass --pin-audit or set TAXONTOY_PIN_AUDIT", missing_audit.stderr)
            both = run([
                "reconcile", str(table_path), "--graph", str(graph_path),
                "--manifest", str(graph_path), "--pin-audit", str(audit_path),
            ])
            self.assertIn("pass --graph or --manifest", both.stderr)
            blank = os.environ.copy()
            blank["TAXONTOY_PIN_AUDIT"] = "   "
            blank["TAXONTOY_GRAPH"] = "   "
            blank["TAXONTOY_MANIFEST"] = "   "
            unset = run(["reconcile", str(table_path)], env=blank)
            self.assertIn("pass --graph or --manifest", unset.stderr)
            lone = {
                "schema": "gh-identity-pin-audit/1",
                "results": [{
                    "repository": "actions/checkout",
                    "commit": _sha("f"),
                    "pins": [{"file": ".github/workflows/missing.yml"}],
                }],
            }
            lone_path = write_json(tmp, "lone.json", lone)
            listed = run([
                "reconcile", str(ROOT / "otl-relations.json"),
                "--manifest", str(ROOT / "fixtures" / "repos" / "manifest.json"),
                "--pin-audit", str(lone_path),
                "--repo", "myon-bioinformatics/OnTautoLogy",
                "--format", "text",
            ])
            self.assertEqual(listed.returncode, 1, listed.stderr + listed.stdout)
            self.assertIn("only_taxontoy myon-bioinformatics/ontautology", listed.stdout)
            self.assertIn("only_pin_audit myon-bioinformatics/ontautology .github/workflows/missing.yml", listed.stdout)
            self.assertEqual(
                taxontoy._audit_pin(
                    {"repository": "actions/checkout", "commit": _sha("a"), "file": "ci.yml"},
                    {},
                    "",
                )["source"],
                "",
            )

    def test_reconcile_rejects_a_pin_audit_that_is_not_the_schema(self):
        table = minimal_table()
        table["repos"] = {"workflow-pin": "dependency"}
        table["direction"]["workflow-pin"] = "forward"
        table["excluded"]["repos"] = []
        graph = {"edges": []}
        cases = [
            ({"schema": "other"}, "expected gh-identity-pin-audit/1"),
            ({"schema": "gh-identity-pin-audit/1", "results": {}}, "results must be an array"),
            ({"schema": "gh-identity-pin-audit/1", "results": ["nope"]}, "must be an object"),
            ({"schema": "gh-identity-pin-audit/1", "results": [{"pins": "nope"}]}, "pins must be an array"),
            ({"schema": "gh-identity-pin-audit/1", "results": [{"pins": ["nope"]}]}, "pins must contain objects"),
            ({"schema": "gh-identity-pin-audit/1", "results": [{"repository": "actions/checkout", "commit": "zz", "pins": [{"file": "ci.yml"}]}]}, "commit must be 40 hex"),
            ({"schema": "gh-identity-pin-audit/1", "results": [{"repository": "actions/checkout", "commit": _sha("a"), "pins": [{}]}]}, "must name a repository and a file"),
            ({"schema": "gh-identity-pin-audit/1", "results": [{"pins": [{"repository": None, "commit": _sha("a"), "file": "ci.yml"}]}]}, "must name a repository and a file"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            table_path = write_json(tmp, "table.json", table)
            graph_path = write_json(tmp, "graph.json", graph)
            for index, (payload, message) in enumerate(cases):
                audit_path = write_json(tmp, f"audit-{index}.json", payload)
                result = run([
                    "reconcile", str(table_path), "--graph", str(graph_path),
                    "--pin-audit", str(audit_path), "--repo", "example/app",
                ])
                self.assertEqual(result.returncode, 1, payload)
                self.assertIn(message, result.stderr)
            manifest = write_json(tmp, "manifest.json", {"sources": []})
            audit_path = write_json(tmp, "ok.json", {"schema": "gh-identity-pin-audit/1", "results": []})
            via_manifest = run([
                "reconcile", str(table_path), "--manifest", str(manifest),
                "--pin-audit", str(audit_path),
            ])
            self.assertEqual(via_manifest.returncode, 1)
            self.assertIn("sources", via_manifest.stderr)

    def test_remaining_branches_are_executed(self):
        self.assertFalse(taxontoy._is_v3(None))
        self.assertFalse(taxontoy._is_v3({"schema": taxontoy.SCHEMA_V2}))
        self.assertIsNone(taxontoy._alias_target({
            "schema": taxontoy.SCHEMA_ID,
            "excluded": {"audit": [
                "nope",
                {"relation": "other", "kind": "alias", "alias_of": "dependsOn"},
                {"relation": "depends_on", "kind": "out_of_scope"},
                {"relation": "depends_on", "kind": "alias", "alias_of": " "},
            ]},
        }, "depends_on"))
        range_only = _v3_table()
        range_only["properties"]["relations"]["belongsTo"] = {
            "range": {"types": ["Repository"], "since": "2026-11-21", "reason": "target"},
        }
        self.assertTrue(taxontoy.property_violations(range_only, [
            {"relation": "belongsTo", "source": "a", "target": "b"},
        ], {"a": "File", "b": "Commit"})[0])
        with self.assertRaises(ValueError):
            taxontoy._workflow_pin_records({"repos": {"subtree": "hierarchy"}}, [])
        domain_only = _v3_table()
        domain_only["properties"]["relations"] = {
            "belongsTo": {"domain": {"types": ["File"], "since": "2026-11-21", "reason": "source"}},
        }
        self.assertEqual(
            taxontoy._type_gaps(domain_only, {"belongsTo": []}, {"a": "File"}, domain_only["audit"], {"belongsTo"}, set())
            ["relations"]["belongsTo"]["gap"]["declared_only"],
            [{"source": "File", "target": None}],
        )
        open_range = _v3_table()
        open_range["properties"]["relations"] = {
            "belongsTo": {"range": {"types": ["Repository"], "since": "2026-11-21", "reason": "target"}},
        }
        self.assertEqual(
            taxontoy._type_gaps(open_range, {"belongsTo": []}, {"a": "File"}, open_range["audit"], {"belongsTo"}, set())
            ["relations"]["belongsTo"]["gap"]["declared_only"],
            [{"source": None, "target": "Repository"}],
        )
        self.assertEqual(taxontoy._type_declarations(None), {})
        self.assertEqual(
            taxontoy._type_declarations({"properties": {"relations": {"belongsTo": {"domain": {"types": "File"}}}}}),
            {},
        )
        self.assertEqual(taxontoy._since_reason({"since": "2026-11-21"}, "where", []), None)
        broken = _v3_table()
        broken["audit"] = []
        self.assertIn("audit: profile must be an object", taxontoy.validate_document(broken))
        types = _v3_table()
        types["properties"]["relations"]["belongsTo"]["domain"]["types"] = "File"
        self.assertIn("types must be a non-empty array", " ".join(taxontoy.validate_document(types)))
        self_alias = _v3_table()
        self_alias["excluded"]["audit"][2]["alias_of"] = "depends_on"
        found, _notes = taxontoy.property_candidates(self_alias, {
            "dependsOn": [{"source": "a", "target": "b"}],
            "depends_on": [{"source": "a", "target": "b"}],
        }, {})
        self.assertFalse(next(item["declared"] for item in found if item["detection"] == "separator"))
        weight = taxontoy._edge_weight({"novel": [{}]}, [], [], ["novel"], 0)
        self.assertEqual(weight["unclassified"], 1)
        self.assertFalse(taxontoy._pair_inside({"domain": ["File"]}, "File", None))
        self.assertFalse(taxontoy._pair_inside({"range": ["Repository"]}, "File", "Commit"))
        self.assertEqual(taxontoy._declared_pairs({"range": ["Repository"]}, set()), {(None, "Repository")})
        ghost = _v3_table()
        ghost["properties"]["relations"]["ghost"] = {
            "range": {"types": ["Repository"], "since": "2026-11-21", "reason": "open domain"},
        }
        gaps = taxontoy._type_gaps(
            ghost,
            {"ghost": [{"source": "a", "target": "missing"}], "belongsTo": [{"source": "loose", "target": "repo"}]},
            {"a": "File", "repo": "Repository"},
            ghost["audit"],
            {"belongsTo"},
            {"ghost"},
        )
        self.assertIn("ghost", gaps["relations"])
        self.assertIsNone(gaps["relations"]["belongsTo"]["actual"][0]["source"])
        catalog = minimal_table()
        with self.assertRaises(ValueError):
            taxontoy._workflow_pin_records(None, [])
        with self.assertRaises(ValueError):
            taxontoy._workflow_pin_records({"repos": []}, [])
        sha = _sha("a")
        _records, skipped = taxontoy._workflow_pin_records(
            {**catalog, "repos": {"workflow-pin": "dependency"}},
            [
                {"kind": "workflow-pin", "source": "example/app", "target": "actions/checkout", "path": "ci.yml", "pin": None},
                {"kind": "workflow-pin", "source": "example/app", "target": "", "path": "ci.yml", "pin": sha},
                {"kind": "workflow-pin", "source": "example/app", "target": "actions/checkout", "path": 1, "pin": sha},
                {"kind": "workflow-pin", "source": "example/app", "target": "actions/checkout", "path": "  ", "pin": sha},
                {"kind": "workflow-pin", "source": "example/app", "target": "actions/checkout", "path": "ci.yml", "pin": sha},
            ],
        )
        self.assertEqual(skipped, 4)
        with tempfile.TemporaryDirectory() as tmp:
            array_path = Path(tmp) / "array.json"
            array_path.write_text("[]\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                taxontoy._read_pin_audit(array_path, "")
            audit_path = write_json(tmp, "audit.json", {
                "schema": "gh-identity-pin-audit/1",
                "results": [{"repository": "actions/checkout", "pins": [{"commit": 1, "file": "ci.yml"}]}],
            })
            with self.assertRaises(ValueError):
                taxontoy._read_pin_audit(audit_path, "example/app")
            with self.assertRaises(ValueError):
                taxontoy._audit_pin({"repository": "actions/checkout", "commit": sha, "file": 1}, {}, "example/app")
            with self.assertRaises(ValueError):
                taxontoy._audit_pin({"repository": "actions/checkout", "commit": sha, "file": " "}, {}, "example/app")
            table = _v3_table()
            table["properties"]["relations"]["dependsOn"] = {
                "domain": {"types": ["PullRequest"], "since": "2026-11-21", "reason": "one side"},
            }
            table["properties"]["relations"]["duplicates"] = {
                "range": {"types": ["PullRequest"], "since": "2026-11-21", "reason": "other side"},
            }
            table_path = write_json(tmp, "table.json", table)
            graphs = fixture_graphs()
            graphs["audit"]["nodes"] = [{"id": "a", "type": "PullRequest"}, {"id": "b", "type": "PullRequest"}, {"id": "c", "type": "PullRequest"}]
            graphs["audit"]["edges"].append({"relation": "belongsTo", "source": "a", "target": "gone"})
            args = ["coverage", str(table_path), "--onto", ONTO, "--format", "text"]
            for wave, payload in graphs.items():
                args.extend(["--graph", f"{wave}={write_json(tmp, f'{wave}.json', payload)}"])
            text = run(args)
            self.assertIn("range -", text.stdout)
            self.assertIn("domain -", text.stdout)
            match_audit = {
                "schema": "gh-identity-pin-audit/1",
                "results": [{
                    "repository": "actions/checkout",
                    "commit": sha,
                    "pins": [{"file": ".github/workflows/ci.yml"}],
                }],
            }
            match_path = write_json(tmp, "match.json", match_audit)
            graph = {"edges": [{
                "kind": "workflow-pin", "source": "example/app", "target": "actions/checkout",
                "path": ".github/workflows/ci.yml", "pin": sha,
            }]}
            graph_path = write_json(tmp, "graph.json", graph)
            pin_table = minimal_table()
            pin_table["repos"] = {"workflow-pin": "dependency"}
            pin_table["direction"]["workflow-pin"] = "forward"
            pin_table["excluded"]["repos"] = []
            pin_table_path = write_json(tmp, "pins.json", pin_table)
            matched = run([
                "reconcile", str(pin_table_path), "--graph", str(graph_path),
                "--pin-audit", str(match_path), "--repo", "example/app", "--format", "text",
            ])
            self.assertEqual(matched.returncode, 0, matched.stderr + matched.stdout)
            self.assertIn(f"same example/app .github/workflows/ci.yml actions/checkout {sha}", matched.stdout)
            shifted = run([
                "reconcile", str(pin_table_path), "--graph", str(graph_path),
                "--pin-audit", str(write_json(tmp, "shifted.json", {
                    "schema": "gh-identity-pin-audit/1",
                    "results": [{
                        "repository": "actions/checkout",
                        "commit": _sha("b"),
                        "pins": [{"file": ".github/workflows/ci.yml"}],
                    }],
                })),
                "--repo", "example/app", "--format", "text",
            ])
            self.assertEqual(shifted.returncode, 1, shifted.stderr + shifted.stdout)
            self.assertIn("different_sha example/app", shifted.stdout)
            self.assertNotIn("only_taxontoy example/app", shifted.stdout)
            audit_only = run([
                "reconcile", str(pin_table_path),
                "--graph", str(write_json(tmp, "empty.json", {"edges": []})),
                "--pin-audit", str(match_path),
                "--repo", "example/app", "--format", "text",
            ])
            self.assertIn("only_pin_audit example/app", audit_only.stdout)
            self.assertNotIn("repository example/app\n", run([
                "reconcile", str(pin_table_path), "--graph", str(graph_path),
                "--pin-audit", str(match_path), "--format", "text",
            ]).stdout)
            only_audit = run([
                "reconcile", str(pin_table_path), "--graph", str(graph_path),
                "--pin-audit", str(write_json(tmp, "extra.json", {
                    "schema": "gh-identity-pin-audit/1",
                    "results": [{
                        "repository": "actions/upload-artifact",
                        "commit": _sha("b"),
                        "pins": [{"file": ".github/workflows/ci.yml"}],
                    }],
                })),
                "--format", "json",
            ])
            self.assertEqual(only_audit.returncode, 1)
            self.assertIsNone(json.loads(only_audit.stdout)["repository"])
            rendered = taxontoy._render_text({
                "version": taxontoy.__version__,
                "command": "properties",
                "ok": False,
                "inputs": [],
                "errors": [],
                "violations": [{
                    "property": "domain",
                    "relations": ["belongsTo"],
                    "pair": {"source_type": None, "target_type": None},
                    "edges": [{"kind": "belongsTo", "source": "a", "target": "b"}],
                }],
                "double_distribution": {"count": 0},
                "skipped": 0,
            })
            self.assertIn("source_type missing target_type missing", rendered)

    def test_v3_mutations_are_killed_by_the_functional_snapshot(self):
        source = (ROOT / "taxontoy.py").read_text(encoding="utf-8")
        mutations = [
            (
                "classified_edges + excluded_edges + unclassified_edges + malformed",
                "classified_edges + unclassified_edges + malformed",
            ),
            (
                'if not isinstance(item, dict) or item.get("relation") != relation or item.get("kind") != "alias":',
                'if not isinstance(item, dict) or item.get("relation") != relation or item.get("kind") == "alias":',
            ),
            (
                'if kind == "alias":',
                'if kind == "out_of_scope":',
            ),
            (
                "if seen != sorted(set(seen)):",
                "if False and seen != sorted(set(seen)):",
            ),
            (
                'if _is_v3(table):\n            waves[wave]["edge_weight"] = _edge_weight(',
                'if False:\n            waves[wave]["edge_weight"] = _edge_weight(',
            ),
            (
                'if "domain" in declared and source not in declared["domain"]:\n                found.append({',
                'if False:\n                found.append({',
            ),
            (
                'if "range" in declared and target not in declared["range"]:\n                found.append({',
                'if False:\n                found.append({',
            ),
            (
                "if not _pair_inside(declared, source, target)",
                "if False",
            ),
            (
                "allowed - observed",
                "observed - allowed",
            ),
            (
                "if not right:\n            only_too.extend",
                "if not left:\n            only_too.extend",
            ),
            (
                "if left == right:",
                "if left != right:",
            ),
            (
                'if not isinstance(commit, str) or _SHA.fullmatch(commit.lower()) is None:',
                'if not isinstance(commit, str) or _SHA.fullmatch(commit) is None:',
            ),
            (
                "if repository is None and commit is None:\n        return None",
                "if False and repository is None and commit is None:\n        return None",
            ),
            (
                'target = _endpoint(repository) if repository is not None else None',
                "target = repository if repository is not None else None",
            ),
        ]
        golden = _v3_snapshot(taxontoy)
        killed, survived = [], []
        for old, new in mutations:
            self.assertEqual(source.count(old), 1, old)
            try:
                module = _load_mutant(source.replace(old, new, 1))
                found = _v3_snapshot(module)
            except Exception:
                killed.append(old)
                continue
            if found != golden:
                killed.append(old)
            else:
                survived.append(old)
        sys.stderr.write(f"v3 mutation killed={len(killed)} survived={len(survived)}\n")
        self.assertEqual(survived, [], survived)


def _read_pin_audit_from(module, payload, source):
    import tempfile as _tempfile
    directory = Path(_tempfile.mkdtemp(prefix="pin-audit-"))
    path = directory / "audit.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return module._read_pin_audit(path, source)


def _v3_snapshot(module):
    table = _v3_table()
    bad_alias = _v3_table()
    bad_alias["excluded"]["audit"] = [
        _exclusion("depends_on", "alias", "dependsOn"),
        _exclusion("belongsTo"),
    ]
    graphs = {wave: list(body["edges"]) for wave, body in fixture_graphs().items()}
    graphs["audit"].append({"relation": "belongsTo", "source": "issue", "target": "repo"})
    nodes = {
        "a": "PullRequest", "b": "PullRequest", "c": "PullRequest",
        "issue": "Issue", "repo": "Repository", "other": "Commit",
    }
    edges = [
        {"relation": "belongsTo", "source": "issue", "target": "repo"},
        {"relation": "belongsTo", "source": "a", "target": "other"},
        {"kind": "workflow-pin", "source": "Example/App", "path": ".github/workflows/ci.yml",
         "target": "Actions/Checkout", "pin": _sha("a").upper()},
        {"kind": "workflow-pin", "source": "example/app", "path": ".github/workflows/pages.yml",
         "target": "actions/checkout", "pin": _sha("d")},
    ]
    catalog = minimal_table()
    catalog["repos"] = {"workflow-pin": "dependency"}
    catalog["direction"]["workflow-pin"] = "forward"
    catalog["excluded"]["repos"] = []
    audit = {
        "schema": "gh-identity-pin-audit/1",
        "results": [
            {"repository": "Actions/Checkout", "commit": _sha("a").upper(), "pins": [
                {"file": ".github/workflows/ci.yml", "line": 1},
            ]},
            {"repository": "actions/checkout", "commit": _sha("e"), "pins": [
                {"file": ".github/workflows/pages.yml", "line": 2},
            ]},
            {"repository": None, "commit": None, "pins": [{"file": "secret.yml", "line": 1}]},
            {"repository": "actions/upload-artifact", "commit": _sha("c"), "pins": [
                {"file": ".github/workflows/ci.yml", "source": "example/app"},
            ]},
        ],
    }
    too_pins, too_skipped = module._workflow_pin_records(catalog, edges)
    audit_pins, audit_skipped = _read_pin_audit_from(module, audit, "example/app")

    class _Kg:
        def topological_order(self, _edges):
            return {"cycle": None}

        def relation_cycles(self, _edges):
            return []

    waves, _errors = module.cover_waves(table, graphs, _Kg(), {"audit": nodes, "concepts": {}, "cases": {}})
    return {
        "alias": module._alias_target(table, "depends_on"),
        "covered": waves["audit"].get("edge_weight"),
        "audit_skipped": audit_skipped,
        "bad": module.validate_document(bad_alias),
        "diff": module.reconcile_pins(too_pins, audit_pins),
        "gaps": module._type_gaps(
            table,
            {"belongsTo": edges[:2], "dependsOn": [{"source": "a", "target": "b"}]},
            nodes,
            table["audit"],
            {"belongsTo"},
            {"belongsTo", "dependsOn"},
        ),
        "ok": module.validate_document(table),
        "too_skipped": too_skipped,
        "violations": module.property_violations(table, edges[:2], nodes)[0],
        "weight": module._edge_weight(
            {"dependsOn": [{}], "blocks": [{}, {}]},
            ["dependsOn"],
            ["blocks"],
            [],
            1,
        ),
    }


# The helper used by RelationV3Tests is the module function; tests call it through taxontoy.
taxontoy._read_pin_audit_from = lambda payload, source: _read_pin_audit_from(taxontoy, payload, source)


class ZLineCoverageTests(unittest.TestCase):
    def test_executed_excluded_and_unexecuted_partition_the_file(self):
        child_lines = set()
        child_arcs = set()
        for path in _COVER_DIR.iterdir():
            lines, arcs = branchcov.load(path.read_text(encoding="utf-8"))
            child_lines.update(lines)
            child_arcs.update(arcs)
        self.assertTrue(child_lines, "CLI subprocesses did not record executed lines")
        source_path = ROOT / "taxontoy.py"
        source = source_path.read_text(encoding="utf-8")
        total = len(source.splitlines())
        code = compile(source, str(source_path), "exec")
        executable = set()

        def walk(block):
            for _start, _end, lineno in block.co_lines():
                if lineno:
                    executable.add(lineno)
            for const in block.co_consts:
                if hasattr(const, "co_lines"):
                    walk(const)

        walk(code)
        seen = branchcov.seen_lines() | child_lines
        executed = executable & seen
        excluded = set(range(1, total + 1)) - executable
        unexecuted = executable - seen
        self.assertEqual(len(executed) + len(excluded) + len(unexecuted), total)
        sys.stderr.write(
            f"statement coverage executed={len(executed)} excluded={len(excluded)} "
            f"unexecuted={len(unexecuted)} total={total}\n"
        )
        if unexecuted:
            lines = source.splitlines()
            preview = "\n".join(f"{number}: {lines[number - 1]}" for number in sorted(unexecuted))
            self.fail(f"unexecuted statements {len(unexecuted)}\n{preview}")
        guard = next(number for number, line in enumerate(source.splitlines(), start=1) if line.startswith("if __name__"))
        self.assertIn(guard, child_lines)

        static = branchcov.static_branches(source_path)
        observed = branchcov.seen_arcs() | child_arcs
        branch_total = sum(len(arcs) for arcs in static.values())
        branch_executed = 0
        branch_unexecuted = []
        for key, arcs in static.items():
            for src, dst in arcs:
                if (key, src, dst) in observed:
                    branch_executed += 1
                else:
                    branch_unexecuted.append((key, src, dst))
        branch_excluded = 0
        self.assertEqual(branch_executed + branch_excluded + len(branch_unexecuted), branch_total)
        sys.stderr.write(
            f"branch coverage executed={branch_executed} excluded={branch_excluded} "
            f"unexecuted={len(branch_unexecuted)} total={branch_total}\n"
        )
        if branch_unexecuted:
            preview = "\n".join(
                branchcov.explain(source_path, key, src, dst) for key, src, dst in branch_unexecuted
            )
            self.fail(f"unexecuted branches {len(branch_unexecuted)}\n{preview}")


if __name__ == "__main__":
    unittest.main()
