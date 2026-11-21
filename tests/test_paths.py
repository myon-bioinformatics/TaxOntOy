"""Resolution order for ONTO, and the mutants that would break it.

Inference layouts are temporary directories. Nothing here reads a real sibling checkout.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import branchcov

branchcov.install()

import onto_path
from test_taxontoy import (
    ROOT,
    _COVER_DIR,
    _WRAPPER,
    fixture_graphs,
    minimal_table,
    run,
    write_json,
)

MARKER = "knowledgegraph.py"
STUB = (
    "def relation_cycles(edges):\n"
    "    return []\n"
    "def topological_order(edges):\n"
    "    return {'cycle': None}\n"
)


@contextmanager
def _cleared_roots():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop(onto_path.ONTO_ENV, None)
        os.environ.pop("ONTO_CHECKOUT", None)
        yield


def load_path_mutant(old, new):
    """Execute a one-site edit of the real onto_path.py source."""
    source = Path(onto_path.__file__).read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise AssertionError(f"mutant anchor count {source.count(old)} for {old!r}")
    spec = importlib.util.spec_from_loader("onto_path_mutant", loader=None)
    module = importlib.util.module_from_spec(spec)
    filename = str(Path(tempfile.mkdtemp(prefix="onto-mutant-")) / "onto_path_mutant.py")
    exec(compile(source.replace(old, new, 1), filename, "exec"), module.__dict__)
    return module


def _touch(path, text="marker\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run_script(script, args, *, cwd, env):
    dest = _COVER_DIR / f"{len(list(_COVER_DIR.iterdir()))}.txt"
    return subprocess.run(
        [sys.executable, "-S", "-c", _WRAPPER, str(script), str(dest), str(ROOT / "tests" / "branchcov.py"), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def _cleared_env():
    env = os.environ.copy()
    for key in (
        "ONTO_ROOT", "ONTO_CHECKOUT", "PYTHONPATH", "TAXONTOY_TABLE",
        "TAXONTOY_GRAPH", "TAXONTOY_MANIFEST", "TAXONTOY_PIN_AUDIT", "TAXONTOY_REPO",
    ):
        env.pop(key, None)
    return env


class LayoutTests(unittest.TestCase):
    def test_anchor_may_be_a_file_a_directory_or_omitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            onto = root / "ONTO"
            _touch(onto / MARKER)
            project = root / "TaxOntOy"
            script = _touch(project / "tool.py", "")
            self.assertEqual(onto_path.resolve_root("ONTO", anchor=script), onto.resolve())
            self.assertEqual(onto_path.resolve_root("ONTO", anchor=project), onto.resolve())
            self.assertEqual(onto_path.marker_file("ONTO", anchor=script), (onto / MARKER).resolve())
        self.assertEqual(onto_path._anchor_dir(None), Path(onto_path.__file__).resolve().parent)
        with self.assertRaises(onto_path.RootError) as raised:
            onto_path.resolve_root("missing-checkout", anchor=ROOT)
        self.assertIn("未知の checkout 名", str(raised.exception))

        class BoomMap(dict):
            def __getitem__(self, key):
                raise RuntimeError("boom")

        saved = onto_path.ROOTS
        try:
            onto_path.ROOTS = BoomMap()
            with self.assertRaises(RuntimeError):
                onto_path._spec("ONTO")
        finally:
            onto_path.ROOTS = saved
        self.assertEqual(onto_path.import_roots(), ("ONTO",))
        onto_path.tuple = lambda items: tuple(items)
        try:
            self.assertEqual(onto_path.import_roots(), ("ONTO",))
        finally:
            del onto_path.tuple

    def test_explicit_beats_env_and_env_beats_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            explicit = root / "explicit"
            via_env = root / "from-env"
            sibling_parent = root / "workspace"
            project = sibling_parent / "TaxOntOy"
            _touch(explicit / MARKER)
            _touch(via_env / MARKER)
            _touch(sibling_parent / "ONTO" / MARKER)
            anchor = _touch(project / "taxontoy.py", "")
            with patch.dict(os.environ, {onto_path.ONTO_ENV: str(via_env)}):
                self.assertEqual(
                    onto_path.resolve_root("ONTO", explicit, anchor=anchor),
                    explicit.resolve(),
                )
                self.assertEqual(onto_path.resolve_root("ONTO", anchor=anchor), via_env.resolve())
            with _cleared_roots():
                self.assertEqual(
                    onto_path.resolve_root("ONTO", anchor=anchor),
                    (sibling_parent / "ONTO").resolve(),
                )

    def test_blank_explicit_does_not_fall_through_and_blank_env_does(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            via_env = root / "from-env"
            parent = root / "workspace"
            _touch(via_env / MARKER)
            _touch(parent / "ONTO" / MARKER)
            anchor = _touch(parent / "TaxOntOy" / "tool.py", "")
            with patch.dict(os.environ, {onto_path.ONTO_ENV: str(via_env)}):
                with self.assertRaises(onto_path.RootError) as raised:
                    onto_path.resolve_root("ONTO", "   ", anchor=anchor)
                self.assertIn("ONTO_ROOT", str(raised.exception))
                self.assertNotIn(str(via_env), str(raised.exception))
            with patch.dict(os.environ, {onto_path.ONTO_ENV: "  \t "}):
                self.assertEqual(
                    onto_path.resolve_root("ONTO", anchor=anchor),
                    (parent / "ONTO").resolve(),
                )

    def test_bad_env_does_not_infer_and_names_the_variable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / "not-onto"
            bad.mkdir()
            parent = root / "workspace"
            _touch(parent / "ONTO" / MARKER)
            anchor = _touch(parent / "TaxOntOy" / "tool.py", "")
            with patch.dict(os.environ, {onto_path.ONTO_ENV: str(bad)}):
                with self.assertRaises(onto_path.RootError) as raised:
                    onto_path.resolve_root("ONTO", anchor=anchor)
            message = str(raised.exception)
            self.assertIn("ONTO_ROOT", message)
            self.assertIn(str(bad.resolve()), message)
            self.assertIn(MARKER, message)

    def test_missing_checkout_names_the_env_var(self):
        with tempfile.TemporaryDirectory() as tmp:
            anchor = _touch(Path(tmp) / "alone" / "tool.py", "")
            with _cleared_roots():
                with self.assertRaises(onto_path.RootError) as onto_error:
                    onto_path.resolve_root("ONTO", anchor=anchor)
            self.assertIn("ONTO_ROOT= を渡す", str(onto_error.exception))
            self.assertIn(MARKER, str(onto_error.exception))

    def test_child_checkout_and_grandchild_embedding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            _touch(project / "ONTO" / MARKER, "child\n")
            anchor = _touch(project / "taxontoy.py", "")
            with _cleared_roots():
                self.assertEqual(onto_path.infer_checkout("ONTO", MARKER, anchor), (project / "ONTO").resolve())
                self.assertEqual(onto_path.resolve_root("ONTO", anchor=anchor), (project / "ONTO").resolve())
            nested = root / "parent" / "vendor" / "TaxOntOy"
            _touch(root / "parent" / "ONTO" / MARKER, "ancestor\n")
            deep = _touch(nested / "taxontoy.py", "")
            with _cleared_roots():
                self.assertEqual(
                    onto_path.infer_checkout("ONTO", MARKER, deep),
                    (root / "parent" / "ONTO").resolve(),
                )
                self.assertEqual(
                    onto_path.resolve_root("ONTO", anchor=nested),
                    (root / "parent" / "ONTO").resolve(),
                )

    def test_sibling_beats_a_nested_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "TaxOntOy"
            _touch(root / "ONTO" / MARKER, "sibling\n")
            _touch(project / "ONTO" / MARKER, "child\n")
            anchor = _touch(project / "taxontoy.py", "")
            with _cleared_roots():
                self.assertEqual(onto_path.resolve_root("ONTO", anchor=anchor), (root / "ONTO").resolve())

    def test_a_directory_marker_is_not_a_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ONTO" / MARKER).mkdir(parents=True)
            anchor = _touch(root / "TaxOntOy" / "tool.py", "")
            with _cleared_roots():
                with self.assertRaises(onto_path.RootError) as raised:
                    onto_path.resolve_root("ONTO", anchor=anchor)
            self.assertIn("ONTO_ROOT", str(raised.exception))

    def test_tilde_in_an_explicit_root_is_expanded(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            onto = home / "ONTO"
            _touch(onto / MARKER)
            anchor = _touch(Path(tmp) / "empty" / "tool.py", "")
            with patch.dict(os.environ, {"HOME": str(home), onto_path.ONTO_ENV: ""}):
                os.environ.pop(onto_path.ONTO_ENV, None)
                self.assertEqual(onto_path.resolve_root("ONTO", "~/ONTO", anchor=anchor), onto.resolve())


class OriginTests(unittest.TestCase):
    def test_install_is_first_and_idempotent_and_origin_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            onto = Path(tmp) / "ONTO"
            _touch(onto / MARKER, "marker = True\n")
            entry = str(onto.resolve())
            saved_path = list(sys.path)
            saved_module = sys.modules.get("knowledgegraph")
            sys.modules.pop("knowledgegraph", None)
            try:
                while entry in sys.path:
                    sys.path.remove(entry)
                onto_path.install_root(onto)
                self.assertEqual(sys.path[0], entry)
                onto_path.install_root(onto)
                self.assertEqual(sys.path.count(entry), 1)
                self.assertEqual(
                    onto_path.verify_origin(onto, onto_path.ONTO_MODULES, onto_path.ONTO_ENV),
                    onto.resolve(),
                )
                sys.modules.pop("knowledgegraph", None)
                self.assertEqual(
                    onto_path.verify_origin(onto, onto_path.ONTO_MODULES, onto_path.ONTO_ENV),
                    onto.resolve(),
                )
                missing = types.ModuleType("knowledgegraph")
                missing.__file__ = None
                sys.modules["knowledgegraph"] = missing
                with self.assertRaises(onto_path.RootError) as raised:
                    onto_path.verify_origin(onto, onto_path.ONTO_MODULES, onto_path.ONTO_ENV)
                self.assertIn("(なし)", str(raised.exception))
                self.assertIn("ONTO_ROOT", str(raised.exception))
                wrong = types.ModuleType("knowledgegraph")
                wrong.__file__ = str(Path(tmp) / "other" / MARKER)
                sys.modules["knowledgegraph"] = wrong
                with self.assertRaises(onto_path.RootError) as raised:
                    onto_path.verify_origin(onto, onto_path.ONTO_MODULES, onto_path.ONTO_ENV)
                self.assertIn(str(Path(tmp) / "other" / MARKER), str(raised.exception))
            finally:
                sys.path[:] = saved_path
                if saved_module is None:
                    sys.modules.pop("knowledgegraph", None)
                else:
                    sys.modules["knowledgegraph"] = saved_module

    def test_bind_records_the_root_without_clobbering_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            onto = root / "ONTO"
            _touch(onto / MARKER, "marker = True\n")
            anchor = _touch(root / "app" / "tool.py", "")
            saved_module = sys.modules.get("knowledgegraph")
            saved_path = list(sys.path)
            sys.modules.pop("knowledgegraph", None)
            try:
                with patch.dict(os.environ, {onto_path.ONTO_ENV: "keep-onto"}):
                    found = onto_path.bind(anchor, {"ONTO": onto})
                    self.assertEqual(found["ONTO"], onto.resolve())
                    self.assertEqual(os.environ[onto_path.ONTO_ENV], "keep-onto")
                    self.assertEqual(onto_path.RESOLVED["ONTO"], onto.resolve())
                    named = onto_path.bind(anchor, {"ONTO": onto}, names=("ONTO",))
                    self.assertEqual(named["ONTO"], onto.resolve())
                with _cleared_roots():
                    sys.modules.pop("knowledgegraph", None)
                    found = onto_path.bind(anchor)
                    self.assertEqual(found["ONTO"], onto.resolve())
                    self.assertEqual(os.environ[onto_path.ONTO_ENV], str(onto.resolve()))
                with self.assertRaises(onto_path.RootError):
                    onto_path.bind(anchor, names=("missing",))
            finally:
                sys.path[:] = saved_path
                if saved_module is None:
                    sys.modules.pop("knowledgegraph", None)
                else:
                    sys.modules["knowledgegraph"] = saved_module

    def test_a_root_without_modules_is_not_imported(self):
        saved = dict(onto_path.ROOTS)
        saved_module = sys.modules.get("knowledgegraph")
        saved_path = list(sys.path)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                onto = root / "ONTO"
                plain = root / "Plain"
                _touch(onto / MARKER, "marker = True\n")
                _touch(plain / "marker.txt", "ok\n")
                anchor = _touch(root / "app" / "tool.py", "")
                onto_path.ROOTS["Plain"] = {
                    "env": "PLAIN_ROOT",
                    "dirname": "Plain",
                    "marker": "marker.txt",
                    "modules": (),
                }
                sys.modules.pop("knowledgegraph", None)
                os.environ.pop("PLAIN_ROOT", None)
                with _cleared_roots():
                    self.assertEqual(onto_path.import_roots(), ("ONTO",))
                    found = onto_path.bind(anchor)
                    self.assertEqual(found["Plain"], plain.resolve())
                    self.assertEqual(found["ONTO"], onto.resolve())
                    self.assertEqual(os.environ["PLAIN_ROOT"], str(plain.resolve()))
                onto_path.ROOTS["ONTO"] = {**saved["ONTO"], "modules": ()}
                self.assertEqual(onto_path.import_roots(), ())
        finally:
            onto_path.ROOTS.clear()
            onto_path.ROOTS.update(saved)
            sys.path[:] = saved_path
            if saved_module is None:
                sys.modules.pop("knowledgegraph", None)
            else:
                sys.modules["knowledgegraph"] = saved_module
            os.environ.pop("PLAIN_ROOT", None)


class ArgvTests(unittest.TestCase):
    def test_flags_forms_stop_and_missing_values(self):
        blank = {"ONTO": None}
        self.assertEqual(onto_path.explicit_from_argv([]), blank)
        self.assertEqual(
            onto_path.explicit_from_argv(["graph", "--onto-root", "/onto"]),
            {"ONTO": "/onto"},
        )
        self.assertEqual(
            onto_path.explicit_from_argv(["--onto-root=/onto", "audit"]),
            {"ONTO": "/onto"},
        )
        self.assertEqual(
            onto_path.explicit_from_argv(["--onto-root", "/first", "--onto-root", "/second"]),
            {"ONTO": "/second"},
        )
        self.assertEqual(onto_path.explicit_from_argv(["--", "--onto-root", "/ignored"]), blank)
        self.assertEqual(onto_path.explicit_from_argv(["--onto-root-extra", "/nope"]), blank)
        self.assertEqual(onto_path.explicit_from_argv(["--onto-root="]), {"ONTO": ""})
        with self.assertRaises(onto_path.RootError) as raised:
            onto_path.explicit_from_argv(["--onto-root"])
        self.assertIn("ONTO_ROOT", str(raised.exception))


class CliResolutionTests(unittest.TestCase):
    def test_help_and_validate_do_not_resolve_onto(self):
        env = _cleared_env()
        env["ONTO_ROOT"] = "/no/such/onto"
        for args in (["--help"], ["validate", "--help"], ["coverage", "--help"], ["--version"]):
            result = run(args, env=env)
            self.assertEqual(result.returncode, 0, (args, result.stderr))
            self.assertNotIn("Traceback", result.stderr)
            self.assertNotIn("ONTO_ROOT", result.stderr)
        validated = run(["validate", "--json"], env=env)
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertTrue(json.loads(validated.stdout)["ok"])

    def test_omitted_table_outside_the_repository_is_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run(["validate", "--json"], cwd=tmp, env=_cleared_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = json.loads(result.stdout)["inputs"][0]["path"]
        self.assertTrue(recorded.endswith("otl-relations.json"))
        self.assertTrue(Path(recorded).is_absolute())

    def test_short_command_infers_a_sibling_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "TaxOntOy"
            project.mkdir()
            onto = root / "ONTO"
            onto.mkdir()
            (onto / MARKER).write_text(STUB, encoding="utf-8")
            shutil.copy(ROOT / "taxontoy.py", project / "taxontoy.py")
            shutil.copy(ROOT / "onto_path.py", project / "onto_path.py")
            table = write_json(project, "table.json", minimal_table())
            args = ["coverage", "table.json", "--format", "json"]
            for wave, payload in fixture_graphs().items():
                path = write_json(project, f"{wave}.json", payload)
                args.extend(["--graph", f"{wave}={path.name}"])
            result = _run_script(project / "taxontoy.py", args, cwd=project, env=_cleared_env())
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        body = json.loads(result.stdout)
        self.assertTrue(body["ok"])
        self.assertTrue(body["onto"]["path"].endswith("ONTO/knowledgegraph.py"))
        self.assertTrue(any(item["path"] == "table.json" for item in body["inputs"]))

    def test_relative_onto_root_is_recorded_relative_to_the_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            onto = root / "ONTO"
            onto.mkdir()
            (onto / MARKER).write_text(STUB, encoding="utf-8")
            table = write_json(root, "table.json", minimal_table())
            args = ["coverage", str(table), "--onto-root", "ONTO", "--format", "json"]
            for wave, payload in fixture_graphs().items():
                path = write_json(root, f"{wave}.json", payload)
                args.extend(["--graph", f"{wave}={path}"])
            result = run(args, cwd=root, env=_cleared_env())
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["onto"]["path"], "ONTO/knowledgegraph.py")

    def test_explicit_root_beats_env_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "good"
            bad = root / "bad"
            good.mkdir()
            bad.mkdir()
            (good / MARKER).write_text(STUB, encoding="utf-8")
            table = write_json(root, "table.json", minimal_table())
            graph_args = []
            for wave, payload in fixture_graphs().items():
                path = write_json(root, f"{wave}.json", payload)
                graph_args.extend(["--graph", f"{wave}={path}"])
            env = _cleared_env()
            env["ONTO_ROOT"] = str(good)
            refused = run(
                ["coverage", str(table), "--onto-root", str(bad), "--format", "json", *graph_args],
                env=env,
            )
            self.assertEqual(refused.returncode, 1)
            self.assertEqual(refused.stdout, "")
            self.assertIn("ONTO_ROOT", refused.stderr)
            self.assertNotIn("Traceback", refused.stderr)
            env["ONTO_ROOT"] = str(bad)
            stuck = run(["coverage", str(table), "--format", "json", *graph_args], env=env)
            self.assertEqual(stuck.returncode, 1)
            self.assertIn(str(bad.resolve()), stuck.stderr)
            self.assertNotIn("Traceback", stuck.stderr)
            chosen = run(
                ["coverage", str(table), "--onto-root", str(good), "--format", "json", *graph_args],
                env=env,
            )
            self.assertEqual(chosen.returncode, 0, chosen.stderr)
            missing_flag = run(["--onto-root"], env=_cleared_env())
            self.assertEqual(missing_flag.returncode, 1)
            self.assertIn("ONTO_ROOT", missing_flag.stderr)
            self.assertNotIn("Traceback", missing_flag.stderr)
            self.assertEqual(missing_flag.stderr.count("\n"), 1)

    def test_empty_knowledgegraph_is_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            onto = root / "onto"
            onto.mkdir()
            (onto / MARKER).write_text("", encoding="utf-8")
            table = write_json(root, "table.json", minimal_table())
            args = ["coverage", str(table), "--onto-root", str(onto), "--format", "json"]
            for wave, payload in fixture_graphs().items():
                path = write_json(root, f"{wave}.json", payload)
                args.extend(["--graph", f"{wave}={path}"])
            result = run(args, env=_cleared_env())
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stderr.count("\n"), 1)
        self.assertIn("knowledgegraph", result.stderr)

    def test_pythonpath_cannot_replace_the_resolved_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root / "fake"
            good = root / "good"
            fake.mkdir()
            good.mkdir()
            (fake / MARKER).write_text(
                "def relation_cycles(*_a, **_k):\n"
                "    raise RuntimeError('WRONG_ONTO')\n"
                "def topological_order(*_a, **_k):\n"
                "    raise RuntimeError('WRONG_ONTO')\n",
                encoding="utf-8",
            )
            (good / MARKER).write_text(STUB, encoding="utf-8")
            table = write_json(root, "table.json", minimal_table())
            args = ["coverage", str(table), "--onto-root", str(good), "--format", "json"]
            for wave, payload in fixture_graphs().items():
                path = write_json(root, f"{wave}.json", payload)
                args.extend(["--graph", f"{wave}={path}"])
            env = _cleared_env()
            env["PYTHONPATH"] = str(fake)
            result = run(args, env=env)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertNotIn("WRONG_ONTO", result.stderr)
        self.assertNotIn("WRONG_ONTO", result.stdout)


class ConftestTests(unittest.TestCase):
    def test_usage_error_names_the_env_var_and_checks_origin(self):
        stub = types.ModuleType("pytest")

        class UsageError(Exception):
            pass

        stub.UsageError = UsageError
        saved_pytest = sys.modules.get("pytest")
        saved_module = sys.modules.get("knowledgegraph")
        saved_path = list(sys.path)
        saved_root = os.environ.get("ONTO_ROOT")
        sys.modules["pytest"] = stub
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                onto = root / "ONTO"
                _touch(onto / MARKER, "marker = True\n")
                os.environ["ONTO_ROOT"] = str(onto)
                sys.modules.pop("knowledgegraph", None)
                spec = importlib.util.spec_from_file_location(
                    "toy_conftest_under_test", ROOT / "conftest.py",
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                with _cleared_roots():
                    with tempfile.TemporaryDirectory() as empty_name:
                        empty = Path(empty_name)
                        missing = empty / "nowhere"
                        missing.mkdir()
                        with self.assertRaises(UsageError) as raised:
                            module.activate(missing / "missing.py")
                    self.assertIn("ONTO_ROOT", str(raised.exception))
                    anchor = _touch(root / "app" / "tool.py", "")
                    sys.modules.pop("knowledgegraph", None)
                    found = module.activate(anchor)
                    self.assertEqual(found["ONTO"], onto.resolve())
                    fake = types.ModuleType("knowledgegraph")
                    fake.__file__ = str(root / "other" / MARKER)
                    sys.modules["knowledgegraph"] = fake
                    with self.assertRaises(UsageError) as raised:
                        module.activate(anchor)
                    self.assertIn("出どころ", str(raised.exception))
                    self.assertIn("ONTO_ROOT", str(raised.exception))
                    with patch.object(onto_path, "bind", side_effect=RuntimeError("boom")):
                        with self.assertRaises(RuntimeError):
                            module.activate(anchor)
        finally:
            if saved_pytest is None:
                sys.modules.pop("pytest", None)
            else:
                sys.modules["pytest"] = saved_pytest
            sys.path[:] = saved_path
            if saved_module is None:
                sys.modules.pop("knowledgegraph", None)
            else:
                sys.modules["knowledgegraph"] = saved_module
            if saved_root is None:
                os.environ.pop("ONTO_ROOT", None)
            else:
                os.environ["ONTO_ROOT"] = saved_root


class ResolutionMutationTests(unittest.TestCase):
    def test_swapped_explicit_and_env_is_killed(self):
        mutant = load_path_mutant(
            "    chosen = _explicit_text(explicit)\n"
            "    if chosen is not None:\n"
            "        if chosen == \"\":\n"
            "            raise RootError(_missing(label, env_name, marker))\n"
            "        return _require_marker(chosen, marker, env_name, label)\n"
            "    chosen = _env_text(env_name)\n",
            "    chosen = _env_text(env_name)\n"
            "    if chosen is not None:\n"
            "        if chosen == \"\":\n"
            "            raise RootError(_missing(label, env_name, marker))\n"
            "        return _require_marker(chosen, marker, env_name, label)\n"
            "    chosen = _explicit_text(explicit)\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            explicit = root / "explicit"
            via_env = root / "env"
            _touch(explicit / MARKER, "explicit\n")
            _touch(via_env / MARKER, "env\n")
            anchor = _touch(root / "tool.py", "")
            with patch.dict(os.environ, {onto_path.ONTO_ENV: str(via_env)}):
                self.assertEqual(onto_path.resolve_root("ONTO", explicit, anchor=anchor), explicit.resolve())
                self.assertEqual(mutant.resolve_root("ONTO", explicit, anchor=anchor), via_env.resolve())

    def test_blank_explicit_falling_through_is_killed(self):
        mutant = load_path_mutant(
            "    if not text:\n        return \"\"\n",
            "    if not text:\n        return None\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            via_env = Path(tmp) / "env"
            _touch(via_env / MARKER)
            anchor = _touch(Path(tmp) / "tool.py", "")
            with patch.dict(os.environ, {onto_path.ONTO_ENV: str(via_env)}):
                with self.assertRaises(onto_path.RootError):
                    onto_path.resolve_root("ONTO", " ", anchor=anchor)
                self.assertEqual(mutant.resolve_root("ONTO", " ", anchor=anchor), via_env.resolve())

    def test_skipping_the_marker_check_is_killed(self):
        mutant = load_path_mutant(
            "    if not (path / marker).is_file():\n",
            "    if False and not (path / marker).is_file():\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "empty"
            bad.mkdir()
            with self.assertRaises(onto_path.RootError):
                onto_path.resolve_root("ONTO", bad, anchor=Path(tmp))
            self.assertEqual(mutant.resolve_root("ONTO", bad, anchor=Path(tmp)), bad.resolve())

    def test_dropping_the_env_name_from_the_message_is_killed(self):
        mutant = load_path_mutant(
            '    return f"{label} が見つからない。{env_name}= を渡す（{marker} がある checkout）"\n',
            '    return f"{label} が見つからない。checkout を渡す（{marker}）"\n',
        )
        with tempfile.TemporaryDirectory() as tmp:
            anchor = _touch(Path(tmp) / "tool.py", "")
            with _cleared_roots():
                with self.assertRaises(onto_path.RootError) as real:
                    onto_path.resolve_root("ONTO", anchor=anchor)
                with self.assertRaises(mutant.RootError) as changed:
                    mutant.resolve_root("ONTO", anchor=anchor)
        self.assertIn("ONTO_ROOT=", str(real.exception))
        self.assertNotIn("ONTO_ROOT", str(changed.exception))

    def test_skipping_the_origin_check_is_killed(self):
        mutant = load_path_mutant(
            "    root = Path(root).resolve()\n    problems = []\n",
            "    return Path(root).resolve()\n    problems = []\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            onto = Path(tmp) / "ONTO"
            _touch(onto / MARKER, "marker = True\n")
            saved = sys.modules.get("knowledgegraph")
            try:
                wrong = types.ModuleType("knowledgegraph")
                wrong.__file__ = str(Path(tmp) / "other" / MARKER)
                sys.modules["knowledgegraph"] = wrong
                with self.assertRaises(onto_path.RootError):
                    onto_path.verify_origin(onto, onto_path.ONTO_MODULES, onto_path.ONTO_ENV)
                self.assertEqual(
                    mutant.verify_origin(onto, onto_path.ONTO_MODULES, onto_path.ONTO_ENV),
                    onto.resolve(),
                )
            finally:
                if saved is None:
                    sys.modules.pop("knowledgegraph", None)
                else:
                    sys.modules["knowledgegraph"] = saved

    def test_not_walking_ancestors_is_killed(self):
        mutant = load_path_mutant(
            "    for candidate in (here, *here.parents):\n",
            "    for candidate in (here,):\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "parent" / "vendor" / "TaxOntOy"
            _touch(root / "parent" / "ONTO" / MARKER)
            anchor = _touch(nested / "tool.py", "")
            self.assertEqual(
                onto_path.infer_checkout("ONTO", MARKER, anchor),
                (root / "parent" / "ONTO").resolve(),
            )
            self.assertIsNone(mutant.infer_checkout("ONTO", MARKER, anchor))

    def test_preferring_the_nested_copy_is_killed(self):
        mutant = load_path_mutant(
            "    found = _dir_if_marked(here.parent / dirname, marker)\n"
            "    if found is not None:\n"
            "        return found\n",
            "    found = None\n"
            "    if found is not None:\n"
            "        return found\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "TaxOntOy"
            _touch(root / "ONTO" / MARKER, "sibling\n")
            _touch(project / "ONTO" / MARKER, "child\n")
            anchor = _touch(project / "tool.py", "")
            self.assertEqual(onto_path.infer_checkout("ONTO", MARKER, anchor), (root / "ONTO").resolve())
            self.assertEqual(mutant.infer_checkout("ONTO", MARKER, anchor), (project / "ONTO").resolve())

    def test_invalid_env_falling_through_to_inference_is_killed(self):
        mutant = load_path_mutant(
            "    chosen = _env_text(env_name)\n"
            "    if chosen is not None:\n"
            "        return _require_marker(chosen, marker, env_name, label)\n",
            "    chosen = _env_text(env_name)\n"
            "    if False and chosen is not None:\n"
            "        return _require_marker(chosen, marker, env_name, label)\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / "bad"
            bad.mkdir()
            parent = root / "workspace"
            _touch(parent / "ONTO" / MARKER)
            anchor = _touch(parent / "TaxOntOy" / "tool.py", "")
            with patch.dict(os.environ, {onto_path.ONTO_ENV: str(bad)}):
                with self.assertRaises(onto_path.RootError):
                    onto_path.resolve_root("ONTO", anchor=anchor)
                self.assertEqual(
                    mutant.resolve_root("ONTO", anchor=anchor),
                    (parent / "ONTO").resolve(),
                )

    def test_appending_onto_to_sys_path_is_killed(self):
        mutant = load_path_mutant(
            "    sys.path.insert(0, entry)\n",
            "    sys.path.append(entry)\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            onto = Path(tmp) / "ONTO"
            onto.mkdir()
            saved = list(sys.path)
            try:
                entry = str(onto.resolve())
                while entry in sys.path:
                    sys.path.remove(entry)
                decoy = str(Path(tmp) / "decoy")
                sys.path.insert(0, decoy)
                placed = mutant.install_root(onto)
                self.assertEqual(placed, entry)
                self.assertLess(sys.path.index(decoy), sys.path.index(entry))
                while entry in sys.path:
                    sys.path.remove(entry)
                sys.path.insert(0, decoy)
                self.assertEqual(onto_path.install_root(onto), entry)
                self.assertEqual(sys.path[0], entry)
            finally:
                sys.path[:] = saved

    def test_overwriting_a_preset_root_env_is_killed(self):
        mutant = load_path_mutant(
            '        os.environ.setdefault(spec["env"], str(root))\n',
            '        os.environ[spec["env"]] = str(root)\n',
        )
        with tempfile.TemporaryDirectory() as tmp:
            onto = Path(tmp) / "ONTO"
            _touch(onto / MARKER, "marker = True\n")
            anchor = _touch(Path(tmp) / "app" / "tool.py", "")
            saved = sys.modules.get("knowledgegraph")
            saved_path = list(sys.path)
            sys.modules.pop("knowledgegraph", None)
            try:
                with patch.dict(os.environ, {onto_path.ONTO_ENV: "keep-onto"}):
                    onto_path.bind(anchor, {"ONTO": onto})
                    self.assertEqual(os.environ[onto_path.ONTO_ENV], "keep-onto")
                    mutant.bind(anchor, {"ONTO": onto})
                    self.assertEqual(os.environ[onto_path.ONTO_ENV], str(onto.resolve()))
            finally:
                sys.path[:] = saved_path
                if saved is None:
                    sys.modules.pop("knowledgegraph", None)
                else:
                    sys.modules["knowledgegraph"] = saved

    def test_skipping_module_installation_is_killed(self):
        mutant = load_path_mutant(
            '        if spec["modules"]:\n',
            '        if False and spec["modules"]:\n',
        )
        with tempfile.TemporaryDirectory() as tmp:
            onto = Path(tmp) / "ONTO"
            _touch(onto / MARKER, "marker = True\n")
            anchor = _touch(Path(tmp) / "app" / "tool.py", "")
            saved = sys.modules.get("knowledgegraph")
            saved_path = list(sys.path)
            saved_root = os.environ.get("ONTO_ROOT")
            try:
                wrong = types.ModuleType("knowledgegraph")
                wrong.__file__ = str(Path(tmp) / "other" / MARKER)
                sys.modules["knowledgegraph"] = wrong
                with self.assertRaises(onto_path.RootError):
                    onto_path.bind(anchor, {"ONTO": onto}, names=("ONTO",))
                self.assertEqual(
                    mutant.bind(anchor, {"ONTO": onto}, names=("ONTO",))["ONTO"],
                    onto.resolve(),
                )
            finally:
                sys.path[:] = saved_path
                if saved is None:
                    sys.modules.pop("knowledgegraph", None)
                else:
                    sys.modules["knowledgegraph"] = saved
                if saved_root is None:
                    os.environ.pop("ONTO_ROOT", None)
                else:
                    os.environ["ONTO_ROOT"] = saved_root

    def test_ignoring_the_equals_form_is_killed(self):
        mutant = load_path_mutant(
            "            if token.startswith(prefix):\n",
            "            if False and token.startswith(prefix):\n",
        )
        argv = ["--onto-root=/onto"]
        self.assertEqual(onto_path.explicit_from_argv(argv), {"ONTO": "/onto"})
        self.assertEqual(mutant.explicit_from_argv(argv), {"ONTO": None})

    def test_skipping_tilde_expansion_is_killed(self):
        mutant = load_path_mutant(
            "def _require_marker(root, marker, env_name, label):\n    path = Path(root).expanduser().resolve()\n",
            "def _require_marker(root, marker, env_name, label):\n    path = Path(root).resolve()\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            onto = home / "ONTO"
            _touch(onto / MARKER)
            anchor = _touch(Path(tmp) / "tool.py", "")
            with patch.dict(os.environ, {"HOME": str(home), onto_path.ONTO_ENV: ""}):
                os.environ.pop(onto_path.ONTO_ENV, None)
                self.assertEqual(onto_path.resolve_root("ONTO", "~/ONTO", anchor=anchor), onto.resolve())
                with self.assertRaises(mutant.RootError):
                    mutant.resolve_root("ONTO", "~/ONTO", anchor=anchor)


if __name__ == "__main__":
    unittest.main()
