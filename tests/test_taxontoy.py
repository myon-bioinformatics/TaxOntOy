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
_SEEN = set()
_COVER_DIR = Path(tempfile.mkdtemp(prefix="taxontoy-lines-"))
_TARGET_SUFFIX = f"{os.sep}taxontoy.py"


def _hook(frame, event, arg):
    if event == "line" and frame.f_code.co_filename.endswith(_TARGET_SUFFIX):
        _SEEN.add(frame.f_lineno)
    return _hook


sys.settrace(_hook)

import taxontoy


_WRAPPER = """
import os, runpy, sys, trace
from pathlib import Path
script, dest = sys.argv[1], sys.argv[2]
sys.argv = [script, *sys.argv[3:]]
tracer = trace.Trace(count=1, trace=0)
status = 0
try:
    tracer.runctx(
        "runpy.run_path(script, run_name='__main__')",
        {"runpy": runpy, "script": script},
        {},
    )
except SystemExit as exc:
    status = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
lines = []
for (filename, lineno), count in tracer.results().counts.items():
    if count and os.path.abspath(filename) == os.path.abspath(script):
        lines.append(str(lineno))
Path(dest).write_text("\\n".join(sorted(lines, key=int)) + ("\\n" if lines else ""), encoding="utf-8")
raise SystemExit(status)
"""


def run(args, *, cwd=None, env=None):
    dest = _COVER_DIR / f"{len(list(_COVER_DIR.iterdir()))}.txt"
    return subprocess.run(
        [sys.executable, "-S", "-c", _WRAPPER, str(ROOT / "taxontoy.py"), str(dest), *args],
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
        self.assertFalse(taxontoy._iso_date("nope"))
        self.assertFalse(taxontoy._iso_date("abcd-ef-gh"))
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
        samples.append(class_both)
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
        ]
        graphs["audit"]["edges"].extend([
            {"source": "a", "relation": "depends-on", "target": "b"},
            {"source": "a", "relation": "DependsOn", "target": "b"},
            {"source": "run", "relation": "belongsTo", "target": "repo"},
            {"source": "a", "relation": "belongsTo", "target": "repo"},
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
        polysemy = next(item for item in audit if item["detection"] == "polysemy")
        self.assertEqual(polysemy["relations"], ["belongsTo"])
        self.assertFalse(polysemy["declared"])
        self.assertFalse(polysemy["folded"])
        self.assertGreaterEqual(len(polysemy["patterns"]), 2)
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


class ZLineCoverageTests(unittest.TestCase):
    def test_executed_excluded_and_unexecuted_partition_the_file(self):
        child = set()
        for path in _COVER_DIR.iterdir():
            text = path.read_text(encoding="utf-8")
            child.update(int(line) for line in text.split() if line)
        self.assertTrue(child, "CLI subprocesses did not record executed lines")
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
        seen = set(_SEEN) | child
        executed = executable & seen
        excluded = set(range(1, total + 1)) - executable
        unexecuted = executable - seen
        self.assertEqual(len(executed) + len(excluded) + len(unexecuted), total)
        sys.stderr.write(
            f"line coverage executed={len(executed)} excluded={len(excluded)} "
            f"unexecuted={len(unexecuted)} total={total}\n"
        )
        if unexecuted:
            lines = source.splitlines()
            preview = "\n".join(f"{number}: {lines[number - 1]}" for number in sorted(unexecuted))
            self.fail(f"unexecuted {len(unexecuted)}\n{preview}")
        guard = next(number for number, line in enumerate(source.splitlines(), start=1) if line.startswith("if __name__"))
        self.assertIn(guard, child)


if __name__ == "__main__":
    unittest.main()
