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
        "schema": taxontoy.SCHEMA_ID,
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
        self.assertIn("depends_on", data["excluded"]["audit"])
        self.assertIn("overlapsWith", data["excluded"]["audit"])
        self.assertIn("negation", taxontoy.CLASS_DIRECTION)
        self.assertEqual(data["repos"]["subtree"], "hierarchy")
        self.assertEqual(data["repos"]["vendor"], "dependency")
        self.assertEqual(data["schema"], "taxontoy-relations/2")
        self.assertEqual(data["excluded"]["repos"], [])

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
            "declared": False,
            "detection": "separator",
            "folded": False,
            "relations": ["dependsOn", "depends_on"],
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
        cases.append((schema, [f"schema: expected {taxontoy.SCHEMA_ID}"]))

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
            "source": "myon-bioinformatics/Aoi",
            "target": "myon-bioinformatics/BlueProbe",
        }, edges)
        self.assertTrue(any(
            edge["kind"] == "vendor"
            and edge["source"] == "myon-bioinformatics/Aoi"
            and edge["target"] == "myon-bioinformatics/markdown"
            and edge["path"] == "vendor/markdown.py"
            and edge["provenance"]["path"] == "vendor.lock.json"
            for edge in edges
        ))
        self.assertTrue(any(
            edge["kind"] == "vendor"
            and edge["source"] == "myon-bioinformatics/BlueProbe"
            and edge["provenance"]["path"] == "blueprobe/vendor.lock.json"
            for edge in edges
        ))
        self.assertTrue(any(
            edge["kind"] == "workflow-pin"
            and edge["source"] == "myon-bioinformatics/TopOntoLogy"
            and edge["target"] == "myon-bioinformatics/ONTO"
            and edge["pin"] == "8ca1ee5dc8c2714ddcdea6db6774f7168c0e617b"
            for edge in edges
        ))
        self.assertTrue(any(
            edge["kind"] == "consumes"
            and edge["source"] == "myon-bioinformatics/QueRyu"
            and edge["target"] == "myon-bioinformatics/BlueProbe"
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
        self.assertIn("mapping subtree-of subtree", run(
            ["graph", "--manifest", "fixtures/repos/manifest.json", "--format", "text"],
        ).stdout)

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
            self.assertIn(("owner/reusable", _SHA_C), pins)
            self.assertIn(("actions/checkout", _SHA_A), pins)
            self.assertIn(("up/lib", _SHA_B), pins)
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
        self.assertEqual(ended, {"count": 1, "pairs": [{
            "path": "pkg/lib.py", "prefix": "pkg", "repository": "hub",
        }]})
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
        }]), {"count": 0, "pairs": []})
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
                'key = (pair["repository"], pair["path"])\n        if key in seen:\n            continue',
                'key = (pair["repository"], pair["path"])\n        if False:\n            continue',
            ),
            (
                'match = re.match(r"^\\s*(?:-\\s+)?uses:\\s*(\\S+)@([0-9a-f]{40})\\b", line)',
                'match = re.match(r"^\\s*uses:\\s*(\\S+)@([0-9a-f]{40})\\b", line)',
            ),
            (
                'if repository and ref and _github_repo(repository) == repository:',
                "if repository and ref and False:",
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
                'if held_left is not None or held_right is not None:\n        return False',
                "if False:\n        return False",
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
                'if "prefix" in pair:\n            row["prefix"] = pair["prefix"]',
                'if False:\n            row["prefix"] = pair["prefix"]',
            ),
        ]
        manifest = ROOT / "fixtures" / "repos" / "manifest.json"
        table = ROOT / "otl-relations.json"
        graph = ROOT / "fixtures" / "repos" / "graph.json"
        golden = _repo_snapshot(taxontoy, manifest, table, graph)
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
    directory = Path(tempfile.mkdtemp(prefix="taxontoy-repo-"))
    sample = directory / "sample.json"
    sample.write_text(json.dumps({"edges": synthetic[:2]}), encoding="utf-8")
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = module.main([
            "properties", str(table), "--graph", str(sample), "--format", "text", "--strict",
        ])
        try:
            missing = module.main(["graph"])
        except SystemExit as exc:
            missing = exc.code if isinstance(exc.code, int) else 1
    return {
        "code": code,
        "counts": module._relation_counts(edges),
        "double": module._double_distribution(violations),
        "dup": module._double_distribution(violations + violations),
        "holders": sorted({edge["source"] for edge in edges if edge["kind"] == "vendor"}),
        "missing": missing,
        "ordered": [item["relations"] for item in ordered],
        "self": len(self_hits),
        "skipped": skipped,
        "stderr": err.getvalue(),
        "text": out.getvalue(),
        "violations": violations,
    }


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
