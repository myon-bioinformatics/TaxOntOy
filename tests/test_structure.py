import json
import tempfile
import unittest
from pathlib import Path

import taxontoy


class TestStructure(unittest.TestCase):
    def test_contacts_are_unordered_and_separate_from_containment(self):
        root = taxontoy._onto_root(None)
        nodes = [{"id": "m", "type": "Model"}]
        edges = []
        for i, name in enumerate(["G", "CYS", "PSU"]):
            residue, atom = f"r{i}", f"a{i}"
            nodes += [dict(id=residue, type="Residue", residue=name), dict(id=atom, type="Atom")]
            edges += [dict(source="m", relation="hasResidue", target=residue),
                      dict(source=residue, relation="hasAtom", target=atom)]
        for a, b in [(0, 1), (1, 2), (2, 0)]:
            edges.append(dict(source=f"a{a}", relation="nearAtom", target=f"a{b}", symmetric=True))
        graph = dict(schema="structure-graph/1", nodes=nodes, edges=edges)
        result = taxontoy.classify_structure(graph, root)
        self.assertTrue(result["ok"])
        self.assertEqual(result["distinct_contact_pairs"], 3)
        self.assertEqual(result["residue_classes"], {"canonical-nucleotide": 1, "canonical-amino-acid": 1, "other-component": 1})
        edges.append(dict(source="a1", relation="nearAtom", target="a0", symmetric=True))
        edges.append(dict(source="a0", relation="hasAtom", target="m"))
        result = taxontoy.classify_structure(graph, root)
        self.assertFalse(result["ok"])
        self.assertIsNotNone(result["hierarchy"]["cycle"])
        self.assertIn("invalid-undirected-contact", [e["kind"] for e in result["errors"]])

    def test_invalid_shape_and_orphan(self):
        root = taxontoy._onto_root(None)
        for bad in [None, {}, {"schema": "structure-graph/1", "nodes": None}, *(
                dict(schema="structure-graph/1", nodes=[row], edges=[]) for row in [None, {}, {"id":"", "type":"Atom"}])]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                taxontoy.classify_structure(bad, root)
        graph = dict(schema="structure-graph/1", nodes=[dict(id="a", type="Atom")], edges=[
            dict(source="a", target="a", relation="nearAtom", symmetric=True)])
        result = taxontoy.classify_structure(graph, root)
        self.assertEqual({e["kind"] for e in result["errors"]}, {"model-count", "parent-count", "invalid-undirected-contact"})
        graph["nodes"].append(dict(id="b", type="Atom"))
        graph["edges"] = [dict(source="a", target="b", relation="nearAtom")]
        self.assertIn("invalid-undirected-contact", [e["kind"] for e in taxontoy.classify_structure(graph, root)["errors"]])
        with tempfile.TemporaryDirectory() as directory:
            src, out = Path(directory, "graph.json"), Path(directory, "report.json")
            args = ["structure", str(src), "--onto-root", str(root), "--output", str(out)]
            src.write_text(json.dumps(graph))
            self.assertEqual(taxontoy.main(args), 1)
            graph.update(nodes=[dict(id="m", type="Model")], edges=[])
            src.write_text(json.dumps(graph))
            self.assertEqual(taxontoy.main(args), 0)
            self.assertTrue(json.loads(out.read_text())["ok"])
