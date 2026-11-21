"""Validate TaxOntOy's relation table, measure coverage, and rank unclassified names.

The table, manifest, and repository graph default to the files beside this
program. coverage resolves ONTO when it runs, and not before.
"""
from __future__ import annotations

import argparse
import ast
import datetime
import hashlib
import importlib.util
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import onto_path

__version__ = "0.6.0"
SCHEMA_V2 = "taxontoy-relations/2"
SCHEMA_ID = "taxontoy-relations/3"
ACCEPTED_SCHEMAS = (SCHEMA_V2, SCHEMA_ID)
EXCLUSION_KINDS = ("alias", "out_of_scope")
WAVES = ("audit", "concepts", "cases")
REPO_PROFILE = "repos"
WORKFLOW_PIN = "workflow-pin"
PIN_AUDIT_SCHEMA = "gh-identity-pin-audit/1"
TOP_KEYS = {"schema", *WAVES, "direction", "excluded", "properties"}
ALLOWED_KEYS = TOP_KEYS | {REPO_PROFILE}
TYPE_KEYS = ("domain", "range")
_SHA = re.compile(r"[0-9a-f]{40}")
_OWNER = re.compile(r"[A-Za-z0-9_.-]+")
_USES_RE = re.compile(
    r"""^\s*(?:-\s+)?uses:\s*['"]?([^\s'"]+?)@([0-9a-fA-F]{40})['"]?(?:\s|#|$)"""
)
_ROLES = {"heads", "lock", "siblings", "workflow"}
MAPPING = (
    {
        "proposed": "subtree-of",
        "relation": "subtree",
        "source": "child-heads.json optional.subtree_prefix; gh_ops.py subtree-push; fleet kind subtree",
    },
    {
        "proposed": "vendors",
        "relation": "vendor",
        "source": "vendor.lock.json vendor-lock/1 files; fleet kind vendor",
    },
    {
        "proposed": "pins",
        "relation": "workflow-pin",
        "source": "workflow uses owner/repo@SHA and checkout repository plus ref; fleet kind workflow-pin",
    },
    {
        "proposed": "consumes",
        "relation": "consumes",
        "source": "aoi.py SIBLINGS; fleet shared-tools is a separate consumer record",
    },
)
CLASS_FLAGS = ("acyclic", "symmetric", "transitive")
RELATION_FLAGS = (*CLASS_FLAGS, "disjoint_with", "inverse_of")
CLASS_DIRECTION = {
    "affirmation": "forward",
    "dependency": "forward",
    "hierarchy": "forward",
    "hypothesis": "forward",
    "judgment-record": "forward",
    "negation": "forward",
    "overlap": "undirected",
    "similarity": "mutual",
    "unknown-element": "forward",
}


def _profile_names(data):
    names = list(WAVES)
    if isinstance(data, dict) and REPO_PROFILE in data:
        names.append(REPO_PROFILE)
    return names


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path):
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError(f"duplicate key: {key}")
            obj[key] = value
        return obj

    text = Path(path).read_text(encoding="utf-8")
    return json.loads(text, object_pairs_hook=pairs)


def _name_ok(value):
    return isinstance(value, str) and value != "" and value == value.strip()


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def _iso_date(value):
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value, re.ASCII) is None:
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_v3(data):
    return isinstance(data, dict) and data.get("schema") == SCHEMA_ID


def _since_reason(obj, where, errors):
    """Check since and reason on a declaration that may carry other fields."""
    payload = {}
    if "since" in obj:
        payload["since"] = obj["since"]
    if "reason" in obj:
        payload["reason"] = obj["reason"]
    _evidence(payload, where, errors, partner=False)


def _excluded_names(table, profile):
    """Relation names excluded from one profile. Version 2 stores strings; version 3 stores objects."""
    values = table["excluded"][profile]
    if _is_v3(table):
        return [item["relation"] for item in values]
    return list(values)


def _alias_target(table, relation):
    """Canonical relation when ``relation`` is excluded as an alias. Version 2 has none."""
    if not _is_v3(table):
        return None
    excluded = table.get("excluded")
    if not isinstance(excluded, dict):
        return None
    for wave in _profile_names(table):
        values = excluded.get(wave)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict) or item.get("relation") != relation or item.get("kind") != "alias":
                continue
            target = item.get("alias_of")
            if _name_ok(target):
                return target
    return None


def _type_declarations(table):
    """domain and range type lists declared on relations."""
    props = table.get("properties") if isinstance(table, dict) else None
    relations = props.get("relations") if isinstance(props, dict) else None
    found = {}
    if not isinstance(relations, dict):
        return found
    for name, body in relations.items():
        if not isinstance(body, dict):
            continue
        declared = {}
        for key in TYPE_KEYS:
            item = body.get(key)
            if isinstance(item, dict) and isinstance(item.get("types"), list):
                declared[key] = list(item["types"])
        if declared:
            found[name] = declared
    return found


def _evidence(obj, where, errors, *, partner):
    """Check since/reason, and a partner relation when partner is true."""
    if not isinstance(obj, dict):
        errors.append(f"{where}: must be an object")
        return None
    allowed = {"since", "reason", "relation"} if partner else {"since", "reason"}
    for key in sorted(set(obj) - allowed):
        errors.append(f"{where}: unknown field {key}")
    if "since" not in obj or obj.get("since") == "" or (isinstance(obj.get("since"), str) and obj.get("since").strip() == ""):
        errors.append(f"{where}: missing since")
    elif not _iso_date(obj.get("since")):
        errors.append(f"{where}: invalid since")
    reason = obj.get("reason")
    if not isinstance(reason, str) or reason.strip() == "":
        errors.append(f"{where}: missing reason")
    elif reason != reason.strip():
        errors.append(f"{where}: missing reason")
    if not partner:
        return None
    relation = obj.get("relation")
    if not _name_ok(relation):
        errors.append(f"{where}: missing relation")
        return None
    return relation


def _unsorted_names(names):
    """True when names are not sorted unique strings. A non-string makes the list unhashable."""
    for name in names:
        if not isinstance(name, str):
            return True
    return names != sorted(set(names))


def _validate_types(obj, where, errors):
    """Optional domain or range: a sorted list of node types, with since and reason."""
    if not isinstance(obj, dict):
        errors.append(f"{where}: must be an object")
        return
    for key in sorted(set(obj) - {"types", "since", "reason"}):
        errors.append(f"{where}: unknown field {key}")
    types = obj.get("types")
    if not isinstance(types, list) or not types:
        errors.append(f"{where}: types must be a non-empty array")
    elif _unsorted_names(types):
        errors.append(f"{where}: types must be sorted and unique")
    else:
        for name in types:
            if not _name_ok(name):
                errors.append(f"{where}: types must be non-empty strings")
    _since_reason(obj, where, errors)


def _validate_properties(data, classified, errors):
    if "properties" not in data:
        return
    props = data["properties"]
    if not isinstance(props, dict):
        errors.append("properties: must be an object")
        return
    for key in sorted(set(props) - {"classes", "relations"}):
        errors.append(f"properties: unknown key {key}")
    for key in ("classes", "relations"):
        if key not in props:
            errors.append(f"properties: missing {key}")
    classes = props.get("classes")
    relations = props.get("relations")
    if not isinstance(classes, dict):
        classes = {}
        if "classes" in props:
            errors.append("properties.classes: must be an object")
    if not isinstance(relations, dict):
        relations = {}
        if "relations" in props:
            errors.append("properties.relations: must be an object")
    known = set(classified)
    excluded = data.get("excluded")
    if isinstance(excluded, dict):
        for wave in _profile_names(data):
            values = excluded.get(wave)
            if not isinstance(values, list):
                continue
            if _is_v3(data):
                known.update(
                    item["relation"]
                    for item in values
                    if isinstance(item, dict) and _name_ok(item.get("relation"))
                )
            else:
                known.update(name for name in values if _name_ok(name))
    class_flags = {}
    for name, body in classes.items():
        where = f"properties.classes.{name}"
        if name not in CLASS_DIRECTION:
            errors.append(f"{where}: unknown class {name!r}")
        if not isinstance(body, dict):
            errors.append(f"{where}: must be an object")
            continue
        flags = set()
        for key in sorted(body):
            if key not in CLASS_FLAGS:
                errors.append(f"{where}: unknown property {key}")
                continue
            _evidence(body[key], f"{where}.{key}", errors, partner=False)
            flags.add(key)
        if "symmetric" in flags and "acyclic" in flags:
            errors.append(f"class {name}: symmetric and acyclic contradict")
        class_flags[name] = flags
    relation_flags = {}
    inverses = {}
    for name, body in relations.items():
        where = f"properties.relations.{name}"
        if name not in known:
            errors.append(f"{where}: unknown relation {name}")
        if not isinstance(body, dict):
            errors.append(f"{where}: must be an object")
            continue
        flags = set()
        for key in sorted(body):
            if key in TYPE_KEYS:
                _validate_types(body[key], f"{where}.{key}", errors)
                continue
            if key not in RELATION_FLAGS:
                errors.append(f"{where}: unknown property {key}")
                continue
            if key == "inverse_of":
                partner = _evidence(body[key], f"{where}.inverse_of", errors, partner=True)
                if partner is not None:
                    if partner not in known:
                        errors.append(f"{where}.inverse_of: unknown relation {partner}")
                    else:
                        inverses[name] = partner
                flags.add(key)
            elif key == "disjoint_with":
                items = body[key]
                if not isinstance(items, list) or not items:
                    errors.append(f"{where}.disjoint_with: must be a non-empty array")
                    continue
                partners = []
                for index, item in enumerate(items):
                    partner = _evidence(item, f"{where}.disjoint_with[{index}]", errors, partner=True)
                    if partner == name:
                        errors.append(f"{where}.disjoint_with: disjoint with itself")
                    elif partner is not None and partner not in known:
                        errors.append(f"{where}.disjoint_with: unknown relation {partner}")
                    if partner is not None:
                        partners.append(partner)
                if partners != sorted(set(partners)):
                    errors.append(f"{where}.disjoint_with: must be sorted and unique")
                flags.add(key)
            else:
                _evidence(body[key], f"{where}.{key}", errors, partner=False)
                flags.add(key)
        if "symmetric" in flags and "acyclic" in flags:
            errors.append(f"relation {name}: symmetric and acyclic contradict")
        relation_flags[name] = flags
    for name, partner in sorted(inverses.items()):
        if inverses.get(partner) != name:
            errors.append("inverse pairs not mutually declared: " + " ".join(sorted((name, partner))))
    for name, (_wave, category) in classified.items():
        combined = class_flags.get(category, set()) | relation_flags.get(name, set())
        split = "symmetric" in combined and "acyclic" in combined
        same_object = (
            ("symmetric" in relation_flags.get(name, set()) and "acyclic" in relation_flags.get(name, set()))
            or ("symmetric" in class_flags.get(category, set()) and "acyclic" in class_flags.get(category, set()))
        )
        if split and not same_object:
            errors.append(f"{name}: symmetric and acyclic contradict")


def _property_rows(data):
    rows = []
    props = data["properties"]
    for scope, label in (("classes", "class"), ("relations", "relation")):
        for name in sorted(props[scope]):
            body = props[scope][name]
            for key in sorted(body):
                if key in TYPE_KEYS:
                    item = body[key]
                    rows.append({
                        "name": name, "property": key, "reason": item["reason"],
                        "scope": label, "since": item["since"], "types": list(item["types"]),
                    })
                elif key == "disjoint_with":
                    for item in body[key]:
                        rows.append({
                            "name": name, "property": key, "reason": item["reason"],
                            "relation": item["relation"], "scope": label, "since": item["since"],
                        })
                elif key == "inverse_of":
                    item = body[key]
                    rows.append({
                        "name": name, "property": key, "reason": item["reason"],
                        "relation": item["relation"], "scope": label, "since": item["since"],
                    })
                else:
                    item = body[key]
                    rows.append({
                        "name": name, "property": key, "reason": item["reason"],
                        "scope": label, "since": item["since"],
                    })
    return rows


def validate_document(data):
    """Return sorted error strings. An empty list means the table is well formed."""
    errors = []
    if not isinstance(data, dict):
        return ["table must be a JSON object"]
    missing = sorted(TOP_KEYS - set(data))
    extra = sorted(set(data) - ALLOWED_KEYS)
    for key in missing:
        errors.append(f"missing key: {key}")
    for key in extra:
        errors.append(f"unknown key: {key}")
    if data.get("schema") not in ACCEPTED_SCHEMAS:
        errors.append(f"schema: expected {SCHEMA_V2} or {SCHEMA_ID}")

    classified = {}
    profiles = _profile_names(data)
    for wave in profiles:
        mapping = data.get(wave)
        if not isinstance(mapping, dict):
            errors.append(f"{wave}: profile must be an object")
            continue
        if not mapping:
            errors.append(f"{wave}: profile has no relations")
        for relation, category in mapping.items():
            if not _name_ok(relation):
                errors.append(f"{wave}: relation names must be non-empty strings")
                continue
            if relation in classified:
                errors.append(f"relation classified in two profiles: {relation}")
            else:
                classified[relation] = (wave, category)
            if category not in CLASS_DIRECTION:
                errors.append(f"{wave}.{relation}: unknown class {category!r}")

    direction = data.get("direction")
    if not isinstance(direction, dict):
        errors.append("direction: must be an object")
        direction = {}
    for relation, value in direction.items():
        if not _name_ok(relation):
            errors.append("direction: names must be non-empty strings")
            continue
        if value not in {"forward", "mutual", "undirected"}:
            errors.append(f"direction.{relation}: unknown direction {value!r}")
        elif relation in classified:
            _wave, category = classified[relation]
            expected = CLASS_DIRECTION.get(category)
            if expected is not None and value != expected:
                errors.append(
                    f"{_wave}.{relation}: class {category} requires {expected}, found {value}"
                )
    for relation in sorted(set(classified) - set(direction)):
        errors.append(f"direction: missing {relation}")
    for relation in sorted(set(direction) - set(classified)):
        errors.append(f"direction: unknown relation {relation}")

    excluded = data.get("excluded")
    if not isinstance(excluded, dict):
        errors.append("excluded: must be an object")
        excluded = {}
    for key in sorted(set(excluded) - set(profiles)):
        errors.append(f"excluded: unknown profile {key}")
    for wave in profiles:
        names = excluded.get(wave)
        if not isinstance(names, list):
            errors.append(f"excluded.{wave}: must be an array")
            continue
        if _is_v3(data):
            _validate_excluded_v3(names, wave, data, classified, errors)
            continue
        if _unsorted_names(names):
            errors.append(f"excluded.{wave}: must be sorted and unique")
        mapping = data.get(wave) if isinstance(data.get(wave), dict) else {}
        for name in names:
            if not _name_ok(name):
                errors.append(f"excluded.{wave}: names must be non-empty strings")
            elif name in mapping:
                errors.append(f"{wave}: {name} is both classified and excluded")
    _validate_properties(data, classified, errors)
    return sorted(set(errors))


def _validate_excluded_v3(entries, wave, data, classified, errors):
    """Version 3 exclusions are objects with since, reason, and kind."""
    seen = []
    mapping = data.get(wave) if isinstance(data.get(wave), dict) else {}
    for index, item in enumerate(entries):
        where = f"excluded.{wave}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: must be an object")
            continue
        for key in sorted(set(item) - {"alias_of", "kind", "reason", "relation", "since"}):
            errors.append(f"{where}: unknown field {key}")
        relation = item.get("relation")
        if not _name_ok(relation):
            errors.append(f"{where}: missing relation")
            relation = None
        else:
            seen.append(relation)
            if relation in mapping:
                errors.append(f"{wave}: {relation} is both classified and excluded")
        kind = item.get("kind")
        if kind not in EXCLUSION_KINDS:
            errors.append(f"{where}: kind must be out_of_scope or alias")
        _since_reason(item, where, errors)
        if kind == "alias":
            alias_of = item.get("alias_of")
            if not _name_ok(alias_of):
                errors.append(f"{where}: missing alias_of")
            elif alias_of == relation:
                errors.append(f"{where}: alias_of itself")
            elif alias_of not in classified:
                errors.append(f"{where}: alias_of unknown relation {alias_of}")
            elif classified[alias_of][0] != wave:
                errors.append(f"{where}: alias_of is classified in {classified[alias_of][0]}")
        elif "alias_of" in item:
            errors.append(f"{where}: alias_of is only for alias")
    if seen != sorted(set(seen)):
        errors.append(f"excluded.{wave}: must be sorted and unique")


def _relation_name(edge):
    if not isinstance(edge, dict):
        raise ValueError("edge")
    if "kind" in edge:
        name = edge["kind"]
    elif "relation" in edge:
        name = edge["relation"]
    else:
        raise KeyError("relation")
    if not _name_ok(name) or not _name_ok(edge.get("source")) or not _name_ok(edge.get("target")):
        raise ValueError("edge")
    return name


def _separator_key(name):
    parts, token = [], []
    for char in name:
        if char in "_- ":
            if token:
                parts.append("".join(token).casefold())
                token = []
            continue
        if char.isupper() and token:
            parts.append("".join(token).casefold())
            token = [char]
        else:
            token.append(char)
    if token:
        parts.append("".join(token).casefold())
    return tuple(parts)


def _alias_candidates(edges_by_name):
    """Report case, separator, inverse, and same-endpoint pairs without merging them."""
    names = sorted(edges_by_name)
    found = []
    by_case, by_sep = {}, {}
    for name in names:
        by_case.setdefault(name.casefold(), []).append(name)
        by_sep.setdefault(_separator_key(name), []).append(name)
    for group in by_case.values():
        distinct = sorted(set(group))
        if len(distinct) > 1:
            found.append({"detection": "case", "folded": False, "relations": distinct})
    for group in by_sep.values():
        distinct = sorted(set(group))
        if len(distinct) > 1 and len({name.casefold() for name in distinct}) > 1:
            found.append({"detection": "separator", "folded": False, "relations": distinct})
    pairs = {
        name: {(edge["source"], edge["target"]) for edge in edges_by_name[name]}
        for name in names
    }
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            left_pairs, right_pairs = pairs[left], pairs[right]
            if not left_pairs or not right_pairs:
                continue
            relations = [left, right]
            if left_pairs == right_pairs:
                found.append({"detection": "same-pairs", "folded": False, "relations": relations})
            elif left_pairs == {(target, source) for source, target in right_pairs}:
                found.append({"detection": "inverse-pair", "folded": False, "relations": relations})
    found.sort(key=lambda item: (item["detection"], item["relations"]))
    return found


def _class_of(table, relation):
    for wave in _profile_names(table):
        mapping = table.get(wave)
        if isinstance(mapping, dict) and relation in mapping:
            return mapping[relation]
    return None


def _flagged(table, relation, flag):
    props = table.get("properties") if isinstance(table.get("properties"), dict) else {}
    classes = props.get("classes") if isinstance(props.get("classes"), dict) else {}
    relations = props.get("relations") if isinstance(props.get("relations"), dict) else {}
    category = _class_of(table, relation)
    class_body = classes.get(category) if isinstance(classes.get(category), dict) else {}
    relation_body = relations.get(relation) if isinstance(relations.get(relation), dict) else {}
    return flag in class_body or flag in relation_body


def _inverse_partner(table, relation):
    props = table.get("properties") if isinstance(table.get("properties"), dict) else {}
    relations = props.get("relations") if isinstance(props.get("relations"), dict) else {}
    body = relations.get(relation)
    if not isinstance(body, dict):
        return None
    inverse = body.get("inverse_of")
    if isinstance(inverse, dict) and _name_ok(inverse.get("relation")):
        return inverse["relation"]
    return None


def _node_types(nodes):
    found = {}
    if not isinstance(nodes, list):
        return found
    for node in nodes:
        if isinstance(node, dict) and _name_ok(node.get("id")) and _name_ok(node.get("type")):
            found[node["id"]] = node["type"]
    return found


def property_candidates(table, edges_by_name, node_types):
    """Alias, inverse, symmetric, and polysemy candidates. Nothing is folded."""
    found = []
    for item in _alias_candidates(edges_by_name):
        declared = False
        relations = item["relations"]
        if item["detection"] == "inverse-pair":
            left, right = relations
            declared = _inverse_partner(table, left) == right and _inverse_partner(table, right) == left
        else:
            for name in relations:
                target = _alias_target(table, name)
                if target in relations and target != name:
                    declared = True
                    break
        row = {"declared": declared, "detection": item["detection"], "folded": False, "relations": relations}
        found.append(row)
    for name in sorted(edges_by_name):
        edges = edges_by_name[name]
        if edges and _symmetric(edges) and any(edge["source"] != edge["target"] for edge in edges):
            found.append({
                "declared": _flagged(table, name, "symmetric"),
                "detection": "symmetric",
                "folded": False,
                "relations": [name],
            })
    notes = []
    if not node_types:
        notes.append("polysemy: skipped; nodes have no type")
    else:
        patterns = defaultdict(lambda: defaultdict(int))
        for name, edges in edges_by_name.items():
            for edge in edges:
                source = node_types.get(edge["source"])
                target = node_types.get(edge["target"])
                if source and target:
                    patterns[name][(source, target)] += 1
        for name in sorted(patterns):
            observed = patterns[name]
            if len(observed) < 2:
                continue
            rows = [
                {"edges": count, "source": source, "target": target}
                for (source, target), count in sorted(observed.items())
            ]
            found.append({
                "declared": False,
                "detection": "polysemy",
                "folded": False,
                "patterns": rows,
                "relations": [name],
            })
    found.sort(key=lambda item: (item["detection"], item["relations"]))
    return found, notes


def _load_knowledgegraph(directory):
    """Load knowledgegraph.py from a resolved root. A broken file is one line."""
    return _load_onto_module(directory, "knowledgegraph", ("relation_cycles", "topological_order"))


def _load_onto_module(directory, name, required):
    path = Path(directory) / (name + ".py")
    try:
        spec = importlib.util.spec_from_file_location("taxontoy_onto_" + name, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"{path}: {error}") from None
    for attr in required:
        if not callable(getattr(module, attr, None)):
            raise ValueError(f"{path} has no {attr}")
    return module


def _onto_root(explicit):
    """Resolve ONTO only for the command that imports it."""
    chosen = None if explicit is None else {"ONTO": explicit}
    try:
        found = onto_path.bind(Path(__file__), chosen, names=("ONTO",))
    except onto_path.RootError as error:
        raise ValueError(str(error)) from None
    except Exception as error:
        raise ValueError(str(error)) from None
    return found["ONTO"]


def _symmetric(edges):
    pairs = {(edge["source"], edge["target"]) for edge in edges}
    return all((target, source) in pairs for source, target in pairs)


def _forward_conflict(edges):
    pairs = {(edge["source"], edge["target"]) for edge in edges}
    return bool(pairs) and _symmetric(edges) and any(source != target for source, target in pairs)


def _edge_weight(grouped, classified_present, excluded_present, unclassified, malformed):
    """Classified edges over every edge in the wave. The ratio is exact, not a float."""
    classified_edges = sum(len(grouped[name]) for name in classified_present)
    excluded_edges = sum(len(grouped[name]) for name in excluded_present)
    unclassified_edges = sum(len(grouped[name]) for name in unclassified)
    total = classified_edges + excluded_edges + unclassified_edges + malformed
    return {
        "classified": classified_edges,
        "classified_ratio": {"denominator": total, "numerator": classified_edges},
        "excluded": excluded_edges,
        "total": total,
        "unclassified": unclassified_edges,
    }


def _pair_inside(declared, source, target):
    """True when both endpoint types are known and each declared side contains its type."""
    if not source or not target:
        return False
    if "domain" in declared and source not in declared["domain"]:
        return False
    if "range" in declared and target not in declared["range"]:
        return False
    return True


def _declared_pairs(declared, observed):
    """Pairs the declaration allows. An open side uses the types actually seen."""
    if "domain" in declared and "range" in declared:
        return {(source, target) for source in declared["domain"] for target in declared["range"]}
    if "domain" in declared:
        targets = {target for _source, target in observed} or {None}
        return {(source, target) for source in declared["domain"] for target in targets}
    sources = {source for source, _target in observed} or {None}
    return {(source, target) for target in declared["range"] for source in sources}


def _type_gaps(table, grouped, node_types, mapping, excluded_names, graph_names):
    """Declared domain/range against the (source type, target type) pairs actually seen."""
    declarations = _type_declarations(table)
    relevant = [
        name for name in sorted(declarations)
        if name in mapping or name in excluded_names or name in graph_names
    ]
    if not relevant:
        return None
    relations = {}
    if not node_types:
        for name in relevant:
            relations[name] = {"declared": declarations[name]}
        return {"note": "types: skipped; nodes have no type", "relations": relations}
    for name in relevant:
        declared = declarations[name]
        counts = defaultdict(int)
        for edge in grouped.get(name, ()):
            source = node_types.get(edge["source"])
            target = node_types.get(edge["target"])
            counts[(source, target)] += 1
        actual = [
            {"edges": count, "source": source, "target": target}
            for (source, target), count in sorted(
                counts.items(), key=lambda item: ((item[0][0] or ""), (item[0][1] or ""), item[1])
            )
        ]
        observed = {(source, target) for (source, target) in counts if source and target}
        allowed = _declared_pairs(declared, observed)
        outside = {
            (source, target)
            for source, target in observed
            if not _pair_inside(declared, source, target)
        }
        relations[name] = {
            "actual": actual,
            "declared": declared,
            "gap": {
                "actual_only": [
                    {"source": source, "target": target}
                    for source, target in sorted(outside)
                ],
                "declared_only": [
                    {"source": source, "target": target}
                    for source, target in sorted(
                        allowed - observed, key=lambda item: ((item[0] or ""), (item[1] or ""))
                    )
                ],
            },
        }
    return {"relations": relations}


def cover_waves(table, graphs, knowledgegraph, node_types=None):
    """Partition each wave's relation names. Candidates are reported and not folded."""
    errors = []
    waves = {}
    types = node_types or {}
    for wave in WAVES:
        grouped = defaultdict(list)
        malformed = 0
        for edge in graphs[wave]:
            try:
                grouped[_relation_name(edge)].append(edge)
            except (KeyError, ValueError):
                malformed += 1
        graph_names = set(grouped)
        mapping = table[wave]
        excluded = _excluded_names(table, wave)
        classified_set = set(mapping) & graph_names
        excluded_set = set(excluded) & graph_names
        unclassified_set = graph_names - set(mapping) - set(excluded)
        stale_set = (set(mapping) | set(excluded)) - graph_names
        classified_present = sorted(classified_set)
        excluded_present = sorted(excluded_set)
        unclassified = sorted(unclassified_set)
        stale = sorted(stale_set)
        total = len(graph_names)
        # Recount from both sides: the four buckets must be pairwise
        # disjoint and must cover graph names union table names.
        # Classified+excluded+unclassified == total is already true of
        # any split of graph_names; including stale and rejecting a
        # classified/excluded overlap makes this fail when the table
        # and the graph disagree about membership.
        universe = graph_names | set(mapping) | set(excluded)
        buckets = (classified_set, excluded_set, unclassified_set, stale_set)
        overlap = any(left & right for i, left in enumerate(buckets) for right in buckets[i + 1:])
        holds = (not overlap) and (not (set(mapping) & set(excluded))) and (set().union(*buckets) == universe)
        if not holds:
            errors.append(f"partition {wave}: graph and table name sets do not close")
        if unclassified:
            errors.append(f"unclassified {wave}: {', '.join(unclassified)}")
        if stale:
            errors.append(f"stale {wave}: {', '.join(stale)}")
        if malformed:
            errors.append(f"malformed {wave}: {malformed}")

        relations = {}
        class_edges = {category: 0 for category in sorted(set(mapping.values()))}
        for name in classified_present:
            edges = grouped[name]
            category = mapping[name]
            direction = table["direction"][name]
            class_edges[category] += len(edges)
            symmetric = _symmetric(edges)
            if direction == "mutual" and not symmetric:
                errors.append(f"direction {wave}.{name}: mutual relation is not symmetric")
            if direction == "forward" and _forward_conflict(edges):
                errors.append(f"direction {wave}.{name}: forward relation is symmetric")
            relations[name] = {
                "acyclic": knowledgegraph.topological_order(edges)["cycle"] is None,
                "class": category,
                "cycle_samples": len(knowledgegraph.relation_cycles(edges)),
                "direction": direction,
                "edges": len(edges),
                "symmetric": symmetric,
            }
        waves[wave] = {
            "alias_candidates": _alias_candidates(grouped),
            "class_edges": class_edges,
            "excluded_edges": {name: len(grouped[name]) for name in excluded_present},
            "names": {
                "classified": classified_present,
                "excluded": excluded_present,
                "stale": stale,
                "unclassified": unclassified,
            },
            "partition": {
                "classified": len(classified_present),
                "excluded": len(excluded_present),
                "holds": holds,
                "stale": len(stale),
                "total": total,
                "unclassified": len(unclassified),
            },
            "relations": relations,
            "unclassified_edges": {name: len(grouped[name]) for name in unclassified},
        }
        if _is_v3(table):
            waves[wave]["edge_weight"] = _edge_weight(
                grouped, classified_present, excluded_present, unclassified, malformed,
            )
            gaps = _type_gaps(table, grouped, types.get(wave, {}), mapping, set(excluded), graph_names)
            if gaps is not None:
                waves[wave]["types"] = gaps
    return waves, sorted(set(errors))


_ALIAS_DETECTIONS = ("case", "inverse-pair", "separator")


def _usable_relation_name(edge):
    """Name this edge would be classified under, or None when it has none."""
    if not isinstance(edge, dict):
        return None
    if "kind" in edge:
        name = edge["kind"]
    elif "relation" in edge:
        name = edge["relation"]
    else:
        return None
    if not _name_ok(name):
        return None
    return name


def _elsewhere(table, profile, relation):
    for wave in _profile_names(table):
        if wave != profile and isinstance(table.get(wave), dict) and relation in table[wave]:
            return wave
    return None


def rank_unclassified(table, edges, profile):
    """Rank names that are not classified for one profile.

    The count is how many edges become classified if that exact name is added
    to the profile. Alias candidates are attached and are not folded in.
    Edges with no usable relation name stay in the nameless bucket.
    """
    if profile not in WAVES:
        raise ValueError(f"unknown profile {profile}")
    classified_names = table[profile]
    excluded = set(_excluded_names(table, profile))
    grouped = defaultdict(list)
    classified = 0
    excluded_edges = 0
    unclassified = 0
    nameless = 0
    for edge in edges:
        name = _usable_relation_name(edge)
        if name is None:
            nameless += 1
            continue
        grouped[name].append(edge)
        if name in classified_names:
            classified += 1
        elif name in excluded:
            excluded_edges += 1
        else:
            unclassified += 1
    alias_input = {}
    for name, items in grouped.items():
        usable = []
        for edge in items:
            source = edge.get("source")
            target = edge.get("target")
            if _name_ok(source) and _name_ok(target):
                usable.append({"source": source, "target": target})
        alias_input[name] = usable
    aliases = [
        item for item in _alias_candidates(alias_input)
        if item["detection"] in _ALIAS_DETECTIONS
    ]
    ranking = []
    for name, items in grouped.items():
        if name in classified_names:
            continue
        related = [dict(item) for item in aliases if name in item["relations"]]
        ranking.append({
            "alias_candidates": related,
            "edges": len(items),
            "elsewhere": _elsewhere(table, profile, name),
            "folded": False,
            "relation": name,
            "status": "excluded" if name in excluded else "unclassified",
        })
    ranking.sort(key=lambda row: (-row["edges"], row["relation"]))
    for index, row in enumerate(ranking, start=1):
        row["rank"] = index
    return {
        "counts": {
            "classified": classified,
            "edges": classified + excluded_edges + unclassified + nameless,
            "excluded": excluded_edges,
            "nameless": nameless,
            "unclassified": unclassified,
        },
        "nameless": {"edges": nameless},
        "ranking": ranking,
    }


def _loads(text, where):
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError(f"{where}: duplicate key: {key}")
            obj[key] = value
        return obj

    try:
        return json.loads(text, object_pairs_hook=pairs)
    except json.JSONDecodeError:
        raise ValueError(f"{where}: invalid JSON") from None


def _norm_path(value):
    """File identity. ``.`` segments drop out. ``..`` and an empty path are not an identity."""
    if not isinstance(value, str) or value != value.strip():
        return None
    raw = value.strip("/")
    if raw == "" or raw == ".":
        return None
    parts = []
    for part in raw.split("/"):
        if part in {"", ".."}:
            return None
        if part == ".":
            continue
        parts.append(part)
    if not parts:
        return None
    return "/".join(parts)


def _paths_overlap(left, right):
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _provenance(row):
    return {"commit": row["commit"], "path": row["path"], "repo": row["repo"]}


def _manifest_rows(path):
    data = _load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list) or not data["sources"]:
        raise ValueError("manifest must be an object with a non-empty sources array")
    root = Path(path).resolve().parent
    rows = []
    for index, item in enumerate(data["sources"]):
        where = f"sources[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where}: must be an object")
        for key in ("commit", "file", "path", "repo", "role"):
            if not _name_ok(item.get(key)):
                raise ValueError(f"{where}: missing {key}")
        if item["role"] not in _ROLES:
            raise ValueError(f"{where}: unknown role {item['role']}")
        if _SHA.fullmatch(item["commit"]) is None:
            raise ValueError(f"{where}: commit must be 40 hex characters")
        relative = Path(item["file"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"{where}: file must stay under the manifest directory")
        candidate = root / relative
        if candidate.is_symlink():
            raise ValueError(f"{where}: file must stay under the manifest directory")
        if not candidate.exists():
            raise ValueError(f"{where}: file is missing")
        full = candidate.resolve()
        if not full.is_relative_to(root) or not full.is_file():
            raise ValueError(f"{where}: file must stay under the manifest directory")
        rows.append({
            "commit": item["commit"],
            "full": full,
            "input_path": str(Path(path).parent / relative),
            "path": item["path"],
            "repo": item["repo"],
            "role": item["role"],
        })
    return rows


def _prefix_rows(rows):
    found = []
    for row in rows:
        if row["role"] != "heads":
            continue
        data = _loads(row["text"], row["path"])
        heads = data.get("heads") if isinstance(data, dict) else None
        if not isinstance(heads, dict):
            raise ValueError(f"{row['path']}: heads must be an object")
        for child, body in heads.items():
            if not _name_ok(child) or not isinstance(body, dict):
                raise ValueError(f"{row['path']}: each head must name a repository")
            optional = body.get("optional")
            if optional is None:
                continue
            if not isinstance(optional, dict):
                raise ValueError(f"{row['path']}: optional must be an object")
            prefix = optional.get("subtree_prefix")
            if prefix is None:
                continue
            if _norm_path(prefix) != prefix or "/" in prefix:
                raise ValueError(f"{row['path']}: subtree prefix must be one path segment")
            found.append({"child": child, "hub": row["repo"], "prefix": prefix, "row": row})
    return found


def _subtree_edges(records):
    return [
        {
            "kind": "subtree",
            "path": item["prefix"],
            "provenance": _provenance(item["row"]),
            "source": _endpoint(item["hub"]),
            "target": _endpoint(item["child"]),
        }
        for item in records
    ]


def _lock_holder(row, records):
    matches = [
        item for item in records
        if item["hub"] == row["repo"] and (
            row["path"] == f"{item['prefix']}/vendor.lock.json" or row["path"].startswith(item["prefix"] + "/")
        )
    ]
    if not matches:
        return row["repo"]
    children = {item["child"] for item in matches}
    if len(children) != 1:
        raise ValueError(f"{row['path']}: subtree prefix matches more than one repository")
    matches.sort(key=lambda item: len(item["prefix"]))
    return matches[-1]["child"]


def _lock_edges(row, records):
    data = _loads(row["text"], row["path"])
    if not isinstance(data, dict) or data.get("schema") != "vendor-lock/1":
        raise ValueError(f"{row['path']}: expected vendor-lock/1")
    files = data.get("files")
    if not isinstance(files, list):
        raise ValueError(f"{row['path']}: files must be an array")
    holder = _lock_holder(row, records)
    edges = []
    for index, item in enumerate(files):
        where = f"{row['path']}: files[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be an object")
        repository = item.get("repository")
        source = item.get("source")
        destination = item.get("destination")
        if not _name_ok(repository) or _norm_path(source) != source or _norm_path(destination) != destination:
            raise ValueError(f"{where} must name repository, source, and destination")
        edge = {
            "kind": "vendor",
            "origin": source,
            "path": destination,
            "provenance": _provenance(row),
            "source": _endpoint(holder),
            "target": _endpoint(repository),
        }
        pin = item.get("commit")
        if pin is not None:
            if not isinstance(pin, str) or _SHA.fullmatch(pin) is None:
                raise ValueError(f"{where} commit must be 40 hex characters")
            edge["pin"] = pin
        edges.append(edge)
    return edges


def _literal(node, where):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_literal(item, where) for item in node.elts]
    if isinstance(node, ast.Tuple):
        copied = []
        for item in node.elts:
            copied.append(_literal(item, where))
        return (*copied,)
    if isinstance(node, ast.Dict):
        return {
            _literal(key, where): _literal(value, where)
            for key, value in zip(node.keys, node.values, strict=True)
        }
    raise ValueError(f"{where}: SIBLINGS must be literal")


def _siblings_table(text, where):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        raise ValueError(f"{where}: SIBLINGS must be literal") from None
    table = None
    for node in tree.body:
        target = value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "SIBLINGS":
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        if isinstance(target, ast.Name) and target.id == "SIBLINGS":
            table = _literal(value, where)
            if not isinstance(table, dict):
                raise ValueError(f"{where}: SIBLINGS must be a dict")
        elif (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "SIBLINGS"
        ):
            if not isinstance(table, dict):
                raise ValueError(f"{where}: SIBLINGS must be a dict")
            table[_literal(target.slice, where)] = _literal(value, where)
    if not isinstance(table, dict):
        raise ValueError(f"{where}: SIBLINGS not found")
    return table


def _child_repo(name, records, hub):
    matches = [item["child"] for item in records if item["hub"] == hub and item["prefix"] == name]
    if len(set(matches)) > 1:
        raise ValueError(f"subtree prefix {name} matches more than one repository")
    if len(matches) == 1:
        return matches[0]
    return None


def _consume_edges(row, records):
    table = _siblings_table(row["text"], row["path"])
    edges = []
    for child_name in sorted(table):
        child = _child_repo(child_name, records, row["repo"])
        if child is None:
            raise ValueError(f"{row['path']}: SIBLINGS key {child_name} is not a subtree prefix")
        directories = table[child_name]
        if not isinstance(directories, (list, tuple)) or not directories:
            raise ValueError(f"{row['path']}: SIBLINGS {child_name} must list directories")
        for directory in directories:
            if not _name_ok(directory):
                raise ValueError(f"{row['path']}: sibling directory must be a non-empty string")
            sibling = _child_repo(directory.split("/")[0], records, row["repo"])
            if sibling is None or sibling == child:
                continue
            edges.append({
                "kind": "consumes",
                "origin": directory,
                "provenance": _provenance(row),
                "source": _endpoint(child),
                "target": _endpoint(sibling),
            })
    return edges


def _github_repo(spec, *, exact=False):
    """owner/name, compared without URL, ``.git``, or letter case.

    ``exact`` keeps only a two-segment name. A workflow path may follow the name.
    """
    if not isinstance(spec, str):
        return None
    text = spec.strip().strip("'\"")
    text = re.sub(r"^(?:https?://github\.com/|git@github\.com:)", "", text, flags=re.IGNORECASE)
    if text.lower().endswith(".git"):
        text = text[:-4]
    parts = [part for part in text.strip("/").split("/") if part]
    if len(parts) < 2 or (exact and len(parts) != 2):
        return None
    owner, name = parts[0], parts[1]
    if _OWNER.fullmatch(owner) is None or _OWNER.fullmatch(name) is None:
        return None
    if owner.startswith(".") or name.startswith("."):
        return None
    return f"{owner.casefold()}/{name.casefold()}"


def _repo_key(value):
    """Identity used when two edges name the same repository."""
    if not _name_ok(value):
        return None
    return _github_repo(value, exact=True) or value.casefold()


def _endpoint(value):
    """Node id shared by every relation. A name that is not a repository stays as given."""
    folded = _repo_key(value)
    if folded is None:
        return value
    return folded


def _workflow_pins(text):
    found = []
    lines = text.splitlines()
    for line in lines:
        match = _USES_RE.match(line)
        if match is None:
            continue
        repo = _github_repo(match.group(1))
        if repo is None:
            continue
        found.append((repo, match.group(2).lower()))
    index = 0
    while index < len(lines):
        step = re.match(r"^(\s*)-\s+\S", lines[index])
        if step is None:
            index += 1
            continue
        indent = len(step.group(1))
        uses = _USES_RE.match(lines[index])
        checkout = uses is not None and _github_repo(uses.group(1)) == "actions/checkout"
        index += 1
        block = []
        while index < len(lines):
            nxt = re.match(r"^(\s*)-\s+\S", lines[index])
            if nxt and len(nxt.group(1)) <= indent:
                break
            block.append(lines[index])
            index += 1
        if not checkout:
            continue
        repository = ref = None
        for line in block:
            repo_match = re.match(r"""^\s+repository:\s*['"]?(\S+?)['"]?\s*$""", line)
            ref_match = re.match(r"""^\s+ref:\s*['"]?([0-9a-fA-F]{40})\b""", line)
            if repo_match:
                repository = repo_match.group(1)
            if ref_match:
                ref = ref_match.group(1)
        canon = _github_repo(repository, exact=True) if repository and ref else None
        if canon is not None:
            found.append((canon, ref.lower()))
    return found


def _pin_edges(row):
    edges = []
    seen = set()
    for repo, pin in _workflow_pins(row["text"]):
        key = (repo, pin, row["path"])
        if key in seen:
            continue
        seen.add(key)
        edges.append({
            "kind": "workflow-pin",
            "path": row["path"],
            "pin": pin,
            "provenance": _provenance(row),
            "source": _endpoint(row["repo"]),
            "target": repo,
        })
    return edges


def build_repo_graph(rows):
    """OTL nodes and edges for repository relations. Rows are manifest entries plus text."""
    records = _prefix_rows(rows)
    edges = _subtree_edges(records)
    for row in rows:
        if row["role"] == "heads":
            continue
        if row["role"] == "lock":
            edges.extend(_lock_edges(row, records))
        elif row["role"] == "siblings":
            edges.extend(_consume_edges(row, records))
        else:
            edges.extend(_pin_edges(row))
    edges.sort(key=lambda edge: json.dumps(edge, ensure_ascii=False, sort_keys=True))
    names = sorted({edge["source"] for edge in edges} | {edge["target"] for edge in edges})
    provenance = [
        {"commit": row["commit"], "path": row["path"], "repo": row["repo"], "role": row["role"]}
        for row in rows
    ]
    provenance.sort(key=lambda item: (item["repo"], item["path"], item["role"]))
    return {
        "edges": edges,
        "mapping": [dict(item) for item in MAPPING],
        "nodes": [{"id": name, "type": "repository"} for name in names],
        "provenance": provenance,
    }


def _graph_from_manifest(path):
    rows = _manifest_rows(path)
    loaded = []
    for row in rows:
        loaded.append({
            "commit": row["commit"],
            "path": row["path"],
            "repo": row["repo"],
            "role": row["role"],
            "text": row["full"].read_text(encoding="utf-8"),
        })
    return build_repo_graph(loaded), [path, *(row["input_path"] for row in rows)]


def _holder_path(edge):
    if "path" not in edge:
        return None
    path = _norm_path(edge.get("path"))
    source = _repo_key(edge.get("source"))
    if source is not None and path is not None:
        return source, path
    return None


def _disjoint_hit(left, right):
    """Same file identity, or the same endpoints when neither edge names a path.

    A path that cannot be normalized does not fall back to the endpoints.
    """
    if "path" in left or "path" in right:
        held_left = _holder_path(left)
        held_right = _holder_path(right)
        if held_left is None or held_right is None:
            return False
        return held_left[0] == held_right[0] and _paths_overlap(held_left[1], held_right[1])
    return (
        _repo_key(left.get("source")) is not None
        and _repo_key(left.get("target")) is not None
        and _repo_key(left.get("source")) == _repo_key(right.get("source"))
        and _repo_key(left.get("target")) == _repo_key(right.get("target"))
    )


def _pair_record(left, right):
    held_left = _holder_path(left)
    held_right = _holder_path(right)
    if held_left is not None and held_right is not None and held_left[0] == held_right[0]:
        longer, shorter = (held_left[1], held_right[1]) if len(held_left[1]) >= len(held_right[1]) else (held_right[1], held_left[1])
        record = {"path": longer, "repository": held_left[0]}
        if shorter != longer:
            record["prefix"] = shorter
        return record
    return {"source": left.get("source"), "target": left.get("target")}


def _type_violations(table, grouped, node_types):
    """One hit per declared side an edge falls outside. Untyped endpoints are outside."""
    if not node_types:
        return []
    found = []
    for name, declared in _type_declarations(table).items():
        for edge in grouped.get(name, ()):
            source = node_types.get(edge.get("source"))
            target = node_types.get(edge.get("target"))
            pair = {"source_type": source, "target_type": target}
            if "domain" in declared and source not in declared["domain"]:
                found.append({
                    "edges": [edge],
                    "pair": dict(pair),
                    "property": "domain",
                    "relations": [name],
                })
            if "range" in declared and target not in declared["range"]:
                found.append({
                    "edges": [edge],
                    "pair": dict(pair),
                    "property": "range",
                    "relations": [name],
                })
    return found


def property_violations(table, edges, node_types=None):
    """Edges that break a declared property. Each disjoint hit keeps both edges."""
    grouped = defaultdict(list)
    skipped = 0
    for edge in edges:
        name = _usable_relation_name(edge)
        if name is None:
            skipped += 1
            continue
        grouped[name].append(edge)
    props = table.get("properties") if isinstance(table, dict) else None
    relations = props.get("relations") if isinstance(props, dict) else None
    declared = []
    if isinstance(relations, dict):
        for _name, body in relations.items():
            if not isinstance(body, dict):
                continue
            items = body.get("disjoint_with")
            if not isinstance(items, list):
                continue
            for item in items:
                partner = item.get("relation") if isinstance(item, dict) else None
                if _name_ok(partner):
                    declared.append((_name, partner))
    seen = set()
    violations = []
    for left_name, right_name in declared:
        key = tuple(sorted((left_name, right_name)))
        if key in seen or left_name == right_name:
            continue
        seen.add(key)
        for left in grouped.get(left_name, ()):
            for right in grouped.get(right_name, ()):
                if not _disjoint_hit(left, right):
                    continue
                chosen = sorted((left, right), key=lambda edge: json.dumps(edge, ensure_ascii=False, sort_keys=True))
                violations.append({
                    "edges": chosen,
                    "pair": _pair_record(left, right),
                    "property": "disjoint_with",
                    "relations": list(key),
                })
    violations.extend(_type_violations(table, grouped, node_types or {}))
    violations.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    return violations, skipped


def _double_distribution(violations):
    """Every subtree/vendor violation. A pathless hit is still one pair."""
    pairs = []
    seen = set()
    for item in violations:
        if item["relations"] != ["subtree", "vendor"]:
            continue
        pair = item["pair"]
        if "repository" in pair and "path" in pair:
            key = ("path", pair["repository"], pair["path"])
            row = {"path": pair["path"], "repository": pair["repository"]}
            if "prefix" in pair:
                row["prefix"] = pair["prefix"]
        elif "source" in pair or "target" in pair:
            key = ("ends", pair.get("source"), pair.get("target"))
            row = {"source": pair.get("source"), "target": pair.get("target")}
        else:
            key = ("repository", pair.get("repository"))
            row = {"repository": pair.get("repository")}
        if key in seen:
            continue
        seen.add(key)
        pairs.append(row)
    pairs.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    return {"count": len(pairs), "pairs": pairs}


def _relation_counts(edges):
    counts = defaultdict(int)
    for edge in edges:
        name = _usable_relation_name(edge)
        if name is not None:
            counts[name] += 1
    return {name: counts[name] for name in sorted(counts)}


def _pin_record(source, path, target, sha):
    return {"path": path, "sha": sha, "source": source, "target": target}


def _workflow_pin_records(table, edges):
    """workflow-pin edges of the repos profile, as (source, path, target, sha) records."""
    repos = table.get(REPO_PROFILE) if isinstance(table, dict) else None
    if not isinstance(repos, dict) or WORKFLOW_PIN not in repos:
        raise ValueError(f"{REPO_PROFILE} profile does not classify {WORKFLOW_PIN}")
    records = []
    skipped = 0
    for edge in edges:
        name = _usable_relation_name(edge)
        if name is None:
            skipped += 1
            continue
        if name != WORKFLOW_PIN:
            continue
        source = _endpoint(edge.get("source"))
        target = _endpoint(edge.get("target"))
        path = edge.get("path")
        sha = edge.get("pin")
        if not isinstance(sha, str) or _SHA.fullmatch(sha.lower()) is None:
            skipped += 1
            continue
        if not _name_ok(source) or not _name_ok(target) or not isinstance(path, str) or path.strip() == "":
            skipped += 1
            continue
        records.append(_pin_record(source, path.strip(), target, sha.lower()))
    return records, skipped


def _audit_pin(item, result, fallback_source):
    """One gh-identity pin, or None when the row redacts the repository."""
    repository = item.get("repository", result.get("repository"))
    commit = item.get("commit", result.get("commit"))
    if repository is None and commit is None:
        return None
    source = item.get("source") if _name_ok(item.get("source")) else fallback_source
    target = _endpoint(repository) if repository is not None else None
    path = item.get("file")
    if not isinstance(commit, str) or _SHA.fullmatch(commit.lower()) is None:
        raise ValueError("pin-audit commit must be 40 hex characters")
    if not _name_ok(target) or not isinstance(path, str) or path.strip() == "":
        raise ValueError("pin-audit pin must name a repository and a file")
    if not _name_ok(source):
        source = ""
    return _pin_record(_endpoint(source) if source else "", path.strip(), target, commit.lower())


def _read_pin_audit(path, source):
    data = _load_json(path)
    if not isinstance(data, dict) or data.get("schema") != PIN_AUDIT_SCHEMA:
        raise ValueError(f"{path}: expected {PIN_AUDIT_SCHEMA}")
    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError(f"{path}: results must be an array")
    pins = []
    skipped = 0
    for index, result in enumerate(results):
        where = f"{path}: results[{index}]"
        if not isinstance(result, dict):
            raise ValueError(f"{where} must be an object")
        items = result.get("pins")
        if not isinstance(items, list):
            raise ValueError(f"{where}.pins must be an array")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"{where}.pins must contain objects")
            record = _audit_pin(item, result, source)
            if record is None:
                skipped += 1
                continue
            pins.append(record)
    return pins, skipped


def reconcile_pins(too_pins, audit_pins):
    """Set difference of workflow pins. Same source, path, and target with another SHA is its own bucket."""
    def place(pin):
        return (pin["source"], pin["path"], pin["target"])

    too_places, audit_places = {}, {}
    for pin in too_pins:
        too_places.setdefault(place(pin), set()).add(pin["sha"])
    for pin in audit_pins:
        audit_places.setdefault(place(pin), set()).add(pin["sha"])
    only_too, only_audit, same, different = [], [], [], []
    for key in sorted(set(too_places) | set(audit_places)):
        left = too_places.get(key, set())
        right = audit_places.get(key, set())
        source, path, target = key
        if not right:
            only_too.extend(_pin_record(source, path, target, sha) for sha in sorted(left))
            continue
        if not left:
            only_audit.extend(_pin_record(source, path, target, sha) for sha in sorted(right))
            continue
        if left == right:
            same.extend(_pin_record(source, path, target, sha) for sha in sorted(left))
            continue
        different.append({
            "path": path,
            "pin_audit": sorted(right),
            "source": source,
            "target": target,
            "taxontoy": sorted(left),
        })
    return {
        "different_sha": different,
        "only_pin_audit": only_audit,
        "only_taxontoy": only_too,
        "same": same,
    }


def _read_edge_graph(path):
    data = _load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("edges"), list):
        raise ValueError(f"{path}: graph JSON must be an object with an edges array")
    return data


def _opt_path(value):
    if value is None:
        return ""
    return _clean(value)


def _bundled(relative):
    """A file shipped beside this program, relative to the working directory when it can be."""
    path = Path(__file__).resolve().parent / relative
    try:
        shown = path.relative_to(Path.cwd().resolve())
    except ValueError:
        return str(path)
    return shown.as_posix()


def _display_path(path):
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _inputs(paths):
    rows = [{"path": path, "sha256": _sha256(path)} for path in paths]
    rows.sort(key=lambda item: item["path"])
    return rows


def _dump(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _render_text(payload):
    lines = [
        f"taxontoy {payload['version']}",
        f"command {payload['command']}",
        f"ok {'yes' if payload['ok'] else 'no'}",
    ]
    for item in payload["inputs"]:
        lines.append(f"input {item['path']} sha256 {item['sha256']}")
    if "onto" in payload:
        onto = payload["onto"]
        lines.append(f"onto {onto['path']} sha256 {onto['sha256']}")
    for error in payload["errors"]:
        lines.append(f"error {error}")
    for row in payload.get("properties", []):
        partner = f" {row['relation']}" if "relation" in row else ""
        types = f" {' '.join(row['types'])}" if row.get("types") else ""
        lines.append(
            f"property {row['scope']} {row['name']} {row['property']}{partner}{types} since {row['since']}"
        )
    for row in payload.get("relations", []):
        lines.append(
            f"relation {row['profile']} {row['relation']} class {row['class']} direction {row['direction']}"
        )
    for wave in payload.get("excluded", {}):
        for entry in payload["excluded"][wave]:
            if isinstance(entry, str):
                lines.append(f"excluded {wave} {entry}")
                continue
            alias = f" alias_of {entry['alias_of']}" if entry.get("kind") == "alias" else ""
            lines.append(
                f"excluded {wave} {entry['relation']} {entry['kind']}{alias} since {entry['since']}"
            )
    for wave in sorted(payload.get("waves", {})):
        body = payload["waves"][wave]
        if "candidates" in body:
            lines.append(f"wave {wave}")
            for note in body.get("notes", []):
                lines.append(f"note {note}")
            for item in body["candidates"]:
                declared = "yes" if item["declared"] else "no"
                patterns = ""
                if "patterns" in item:
                    patterns = " " + " ".join(
                        f"{row['source']}->{row['target']}:{row['edges']}" for row in item["patterns"]
                    )
                lines.append(
                    f"candidate {item['detection']} declared {declared} {' '.join(item['relations'])}{patterns}"
                )
            continue
        part = body["partition"]
        lines.append(f"wave {wave}")
        lines.append(
            "partition total {total} classified {classified} excluded {excluded} "
            "unclassified {unclassified} stale {stale}".format(**part)
        )
        if "edge_weight" in body:
            weight = body["edge_weight"]
            ratio = weight["classified_ratio"]
            lines.append(
                "edges classified {classified} excluded {excluded} unclassified {unclassified} "
                "total {total} ratio {numerator}/{denominator}".format(
                    classified=weight["classified"],
                    excluded=weight["excluded"],
                    unclassified=weight["unclassified"],
                    total=weight["total"],
                    numerator=ratio["numerator"],
                    denominator=ratio["denominator"],
                )
            )
        if "types" in body:
            if "note" in body["types"]:
                lines.append(f"note {body['types']['note']}")
            for name in sorted(body["types"]["relations"]):
                row = body["types"]["relations"][name]
                declared = row["declared"]
                domain = " ".join(declared.get("domain", [])) or "-"
                span = " ".join(declared.get("range", [])) or "-"
                lines.append(f"types {name} domain {domain} range {span}")
                for actual in row.get("actual", []):
                    lines.append(
                        f"actual {name} {actual['source']}->{actual['target']}:{actual['edges']}"
                    )
                gap = row.get("gap", {})
                for kind in ("actual_only", "declared_only"):
                    for pair in gap.get(kind, []):
                        lines.append(f"gap {kind} {name} {pair['source']}->{pair['target']}")
        for name in sorted(body["class_edges"]):
            lines.append(f"class {name} edges {body['class_edges'][name]}")
        for name in sorted(body["relations"]):
            row = body["relations"][name]
            lines.append(
                f"relation {name} class {row['class']} direction {row['direction']} "
                f"edges {row['edges']} acyclic {'yes' if row['acyclic'] else 'no'} "
                f"symmetric {'yes' if row['symmetric'] else 'no'} cycle_samples {row['cycle_samples']}"
            )
        for name in sorted(body["excluded_edges"]):
            lines.append(f"excluded {name} edges {body['excluded_edges'][name]}")
        for name in sorted(body["unclassified_edges"]):
            lines.append(f"unclassified {name} edges {body['unclassified_edges'][name]}")
        for name in body["names"]["stale"]:
            lines.append(f"stale {name}")
        for item in body["alias_candidates"]:
            lines.append(f"candidate {item['detection']} {' '.join(item['relations'])}")
    if "ranking" in payload:
        counts = payload["counts"]
        lines.append(f"profile {payload['profile']}")
        lines.append(
            "edges {edges} classified {classified} excluded {excluded} "
            "unclassified {unclassified} nameless {nameless}".format(**counts)
        )
        for row in payload["ranking"]:
            suffix = f" elsewhere {row['elsewhere']}" if row["elsewhere"] else ""
            lines.append(
                f"rank {row['rank']} {row['relation']} edges {row['edges']} status {row['status']}{suffix}"
            )
            for alias in row["alias_candidates"]:
                lines.append(f"alias {alias['detection']} {' '.join(alias['relations'])}")
        lines.append(f"nameless {payload['nameless']['edges']}")
    if payload.get("command") == "reconcile":
        counts = payload["counts"]
        lines.append(
            "pins only_taxontoy {only_taxontoy} only_pin_audit {only_pin_audit} "
            "different_sha {different_sha} same {same}".format(**counts)
        )
        if payload.get("repository"):
            lines.append(f"repository {payload['repository']}")
        lines.append(f"skipped {payload['skipped']}")
        for row in payload["only_taxontoy"]:
            lines.append(f"only_taxontoy {row['source']} {row['path']} {row['target']} {row['sha']}")
        for row in payload["only_pin_audit"]:
            lines.append(f"only_pin_audit {row['source']} {row['path']} {row['target']} {row['sha']}")
        for row in payload["different_sha"]:
            lines.append(
                f"different_sha {row['source']} {row['path']} {row['target']} "
                f"taxontoy {','.join(row['taxontoy'])} pin_audit {','.join(row['pin_audit'])}"
            )
        for row in payload["same"]:
            lines.append(f"same {row['source']} {row['path']} {row['target']} {row['sha']}")
    if payload.get("command") == "graph":
        lines.append(f"nodes {len(payload['nodes'])}")
        lines.append(f"edges {len(payload['edges'])}")
        for edge in payload["edges"]:
            path = f" path {edge['path']}" if "path" in edge else ""
            origin = f" origin {edge['origin']}" if "origin" in edge else ""
            pin = f" pin {edge['pin']}" if "pin" in edge else ""
            lines.append(f"edge {edge['kind']} {edge['source']} {edge['target']}{path}{origin}{pin}")
        for item in payload["mapping"]:
            lines.append(f"mapping {item['proposed']} {item['relation']}")
    if "violations" in payload:
        lines.append(f"violations {len(payload['violations'])}")
        lines.append(f"double_distribution {payload['double_distribution']['count']}")
        lines.append(f"skipped {payload['skipped']}")
        for item in payload["violations"]:
            pair = item["pair"]
            if "source_type" in pair:
                source_type = pair.get("source_type") or "missing"
                target_type = pair.get("target_type") or "missing"
                ident = f"source_type {source_type} target_type {target_type}"
            elif "path" in pair:
                ident = f"repository {pair['repository']} path {pair['path']}"
            else:
                ident = f"source {pair['source']} target {pair['target']}"
            lines.append(f"violation {item['property']} {' '.join(item['relations'])} {ident}")
            for edge in item["edges"]:
                kind = edge.get("kind", edge.get("relation"))
                path = f" path {edge['path']}" if "path" in edge else ""
                lines.append(f"edge {kind} {edge['source']} {edge['target']}{path}")
    return "\n".join(lines) + "\n"


def _emit(text, output):
    if output is None:
        sys.stdout.write(text)
        return
    Path(output).write_text(text, encoding="utf-8")


def _table_path(args):
    text = _clean(getattr(args, "table", None))
    if text:
        return text
    return _bundled("otl-relations.json")


def _manifest_path(args):
    text = _opt_path(getattr(args, "manifest", None))
    if text:
        return text
    return _bundled("fixtures/repos/manifest.json")


def _graph_choice(args):
    """Return (graph, manifest). Neither flag uses the bundled repository graph."""
    graph_path = _opt_path(getattr(args, "graph", None))
    manifest = _opt_path(getattr(args, "manifest", None))
    if graph_path and manifest:
        raise ValueError("pass --graph or --manifest")
    if manifest:
        return "", manifest
    if graph_path:
        return graph_path, ""
    return _bundled("fixtures/repos/graph.json"), ""


def _format(args):
    if args.json and args.format == "text":
        raise ValueError("--json and --format text disagree")
    if args.json:
        return "json"
    return args.format or args.format_default


def _render(payload, fmt):
    return _dump(payload) if fmt == "json" else _render_text(payload)


def _validate_payload(path, data, errors):
    payload = {
        "command": "validate",
        "errors": errors,
        "inputs": _inputs([path]),
        "ok": not errors,
        "tool": "taxontoy",
        "version": __version__,
    }
    if not errors:
        profiles = _profile_names(data)
        payload["excluded"] = {wave: list(data["excluded"][wave]) for wave in profiles}
        payload["properties"] = _property_rows(data)
        payload["relations"] = [
            {
                "class": data[wave][relation],
                "direction": data["direction"][relation],
                "profile": wave,
                "relation": relation,
            }
            for wave in profiles
            for relation in sorted(data[wave])
        ]
    return payload


def _parse_graphs(values):
    graphs = {wave: [] for wave in WAVES}
    types = {wave: {} for wave in WAVES}
    seen = set()
    for value in values:
        wave, sep, path = value.partition("=")
        path = path.strip()
        if sep != "=" or wave not in WAVES or path == "":
            raise ValueError("use --graph audit|concepts|cases=PATH")
        if path in seen:
            raise ValueError(f"duplicate graph path: {path}")
        seen.add(path)
        data = _load_json(path)
        if not isinstance(data, dict) or not isinstance(data.get("edges"), list):
            raise ValueError(f"{path}: graph JSON must be an object with an edges array")
        graphs[wave].extend(data["edges"])
        types[wave].update(_node_types(data.get("nodes")))
    missing = [wave for wave in WAVES if not graphs[wave]]
    if missing:
        raise ValueError("missing graph for " + ", ".join(missing))
    return graphs, types, sorted(seen)


def classify_structure(graph, root):
    """Classify the relations and components of one SSIS coordinate observation."""
    if not isinstance(graph, dict) or graph.get("schema") != "structure-graph/1":
        raise ValueError("expected structure-graph/1")
    nodes, edges = graph.get("nodes"), graph.get("edges")
    for rows, keys in ((nodes, ("id", "type")), (edges, ("source", "relation", "target"))):
        if not isinstance(rows, list) or any(not isinstance(row, dict) or
                any(not isinstance(row.get(k), str) or not row[k] for k in keys) for row in rows):
            raise ValueError("invalid structure graph nodes or edges")
    vocabulary = {"hasResidue": (["Model"], "Residue"), "hasAtom": (["Residue"], "Atom"),
                  "nearAtom": (["Atom"], "Atom")}
    checker = _load_onto_module(root, "ontology", ("violations",))
    errors = checker.violations(graph, vocabulary)
    hierarchy = [e for e in edges if e["relation"] in ("hasResidue", "hasAtom")]
    order = _load_knowledgegraph(root).topological_order(hierarchy)
    parents = defaultdict(int)
    for edge in hierarchy:
        parents[edge["target"]] += 1
    if sum(n["type"] == "Model" for n in nodes) != 1:
        errors.append({"kind": "model-count"})
    residues = defaultdict(int)
    for node in nodes:
        if node["type"] in ("Atom", "Residue") and parents[node["id"]] != 1:
            errors.append({"kind": "parent-count", "node": node["id"]})
        if node["type"] == "Residue":
            component = node.get("residue")
            if component in ("A", "C", "G", "U", "DA", "DC", "DG", "DT"):
                group = "canonical-nucleotide"
            elif component in "ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL".split():
                group = "canonical-amino-acid"
            else:
                group = "other-component"
            residues[group] += 1
    pairs = set()
    for edge in edges:
        if edge["relation"] != "nearAtom":
            continue
        pair = tuple(sorted((edge["source"], edge["target"])))
        if pair in pairs or pair[0] == pair[1] or edge.get("symmetric") is not True:
            errors.append({"kind": "invalid-undirected-contact", "pair": pair})
        pairs.add(pair)
    if order["cycle"] is not None:
        errors.append({"kind": "containment-cycle"})
    return {"ok": not errors, "errors": errors, "hierarchy": order,
            "containment_edges": len(hierarchy), "distinct_contact_pairs": len(pairs),
            "residue_classes": dict(residues), "source_sha256": graph.get("source_sha256")}


def _add_format(parser, default):
    parser.add_argument("--format", choices=("text", "json"), help="text or json; json is canonical machine output")
    parser.add_argument("--json", action="store_true", help="shorthand for --format json")
    parser.add_argument("--output", type=Path, help="write the report here instead of stdout")
    parser.set_defaults(format_default=default)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="taxontoy", description=__doc__)
    parser.add_argument("--version", action="version", version=f"taxontoy {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    structure = commands.add_parser("structure", help="classify an SSIS coordinate graph and check containment")
    structure.add_argument("graph", type=Path)
    structure.add_argument("--onto-root", type=Path)
    structure.add_argument("--output", type=Path)

    validate = commands.add_parser("validate", help="check the classification table")
    validate.add_argument("table", nargs="?", type=Path)
    _add_format(validate, "text")

    cover = commands.add_parser("coverage", help="partition relation names in OTL graphs")
    cover.add_argument("table", nargs="?", type=Path)
    cover.add_argument("--graph", action="append", default=[], metavar="WAVE=PATH")
    cover.add_argument(
        "--onto-root", type=Path,
        help="ONTO checkout (default: ONTO_ROOT, then a sibling or ancestor checkout)",
    )
    cover.add_argument("--strict", action="store_true", help="exit 2 when coverage is not closed")
    _add_format(cover, "json")

    candidates = commands.add_parser("candidates", help="report alias and property candidates without folding")
    candidates.add_argument("table", nargs="?", type=Path)
    candidates.add_argument("--graph", action="append", default=[], metavar="WAVE=PATH")
    _add_format(candidates, "json")

    rank = commands.add_parser(
        "rank-unclassified",
        help="rank relation names that are not classified for one profile",
    )
    rank.add_argument("graph", type=Path)
    rank.add_argument("table", nargs="?", type=Path)
    rank.add_argument("--profile", required=True, choices=WAVES)
    _add_format(rank, "json")

    graph_cmd = commands.add_parser("graph", help="build an OTL graph of repository relations from explicit paths")
    graph_cmd.add_argument("--manifest", type=Path)
    _add_format(graph_cmd, "json")

    props = commands.add_parser("properties", help="report declared property violations with the offending edges")
    props.add_argument("table", nargs="?", type=Path)
    props.add_argument("--graph", type=Path)
    props.add_argument("--manifest", type=Path)
    props.add_argument("--strict", action="store_true", help="exit 2 when a declared property is violated")
    _add_format(props, "json")

    reconcile = commands.add_parser(
        "reconcile",
        help="diff repos workflow-pin edges against a gh_identity pin-audit file",
    )
    reconcile.add_argument("table", nargs="?", type=Path)
    reconcile.add_argument("--graph", type=Path)
    reconcile.add_argument("--manifest", type=Path)
    reconcile.add_argument("--pin-audit", type=Path, help="gh-identity-pin-audit/1 JSON; TOO does not call GitHub")
    reconcile.add_argument("--repo", help="workflow repository the pin-audit file describes")
    reconcile.add_argument("--strict", action="store_true", help="exit 2 when the pin sets differ")
    _add_format(reconcile, "json")

    if argv is None:
        argv = sys.argv[1:]
    else:
        argv = list(argv)
    try:
        onto_path.explicit_from_argv(argv)
    except onto_path.RootError as error:
        print(f"taxontoy: {error}", file=sys.stderr)
        return 1
    args = parser.parse_args(argv)
    try:
        if args.command == "structure":
            payload = classify_structure(_load_json(args.graph), _onto_root(args.onto_root))
            _emit(_dump(payload), args.output)
            return 0 if payload["ok"] else 1
        fmt = _format(args)
        output = _clean(args.output) or None
        if args.command == "graph":
            manifest = _manifest_path(args)
            built, inputs = _graph_from_manifest(manifest)
            payload = {
                "command": "graph",
                "edges": built["edges"],
                "errors": [],
                "inputs": _inputs(inputs),
                "mapping": built["mapping"],
                "nodes": built["nodes"],
                "ok": True,
                "provenance": built["provenance"],
                "tool": "taxontoy",
                "version": __version__,
            }
            _emit(_render(payload, fmt), output)
            return 0
        table_path = _table_path(args)
        data = _load_json(table_path)
        errors = validate_document(data)
        if args.command == "validate":
            payload = _validate_payload(table_path, data if isinstance(data, dict) else {}, errors)
            _emit(_render(payload, fmt), output)
            return 0 if payload["ok"] else 1
        if errors:
            payload = _validate_payload(table_path, data if isinstance(data, dict) else {}, errors)
            payload["command"] = args.command
            _emit(_render(payload, fmt), output)
            return 1
        if args.command == "rank-unclassified":
            graph_path = _clean(args.graph)
            if not graph_path:
                raise ValueError("pass the graph path")
            graph = _load_json(graph_path)
            if not isinstance(graph, dict) or not isinstance(graph.get("edges"), list):
                raise ValueError(f"{graph_path}: graph JSON must be an object with an edges array")
            ranked = rank_unclassified(data, graph["edges"], args.profile)
            payload = {
                "command": "rank-unclassified",
                "counts": ranked["counts"],
                "errors": [],
                "inputs": _inputs([graph_path, table_path]),
                "nameless": ranked["nameless"],
                "ok": True,
                "profile": args.profile,
                "ranking": ranked["ranking"],
                "tool": "taxontoy",
                "version": __version__,
            }
            _emit(_render(payload, fmt), output)
            return 0
        if args.command == "properties":
            graph_path, manifest = _graph_choice(args)
            if manifest:
                built, inputs = _graph_from_manifest(manifest)
                edges = built["edges"]
                node_types = _node_types(built["nodes"])
                input_paths = [table_path, *inputs]
            else:
                document = _read_edge_graph(graph_path)
                edges = document["edges"]
                node_types = _node_types(document.get("nodes"))
                input_paths = [table_path, graph_path]
            violations, skipped = property_violations(data, edges, node_types)
            payload = {
                "command": "properties",
                "counts": _relation_counts(edges),
                "double_distribution": _double_distribution(violations),
                "errors": [],
                "inputs": _inputs(input_paths),
                "ok": not violations,
                "skipped": skipped,
                "tool": "taxontoy",
                "version": __version__,
                "violations": violations,
            }
            _emit(_render(payload, fmt), output)
            if violations and args.strict:
                return 2
            return 0 if payload["ok"] else 1
        if args.command == "reconcile":
            graph_path, manifest = _graph_choice(args)
            audit_path = _opt_path(getattr(args, "pin_audit", None))
            if not audit_path:
                raise ValueError("pass --pin-audit")
            repository = _clean(getattr(args, "repo", None))
            source = _endpoint(repository) if repository else ""
            if manifest:
                built, inputs = _graph_from_manifest(manifest)
                edges = built["edges"]
                input_paths = [table_path, audit_path, *inputs]
            else:
                document = _read_edge_graph(graph_path)
                edges = document["edges"]
                input_paths = [table_path, audit_path, graph_path]
            too_pins, too_skipped = _workflow_pin_records(data, edges)
            audit_pins, audit_skipped = _read_pin_audit(audit_path, source)
            diff = reconcile_pins(too_pins, audit_pins)
            differed = bool(diff["only_taxontoy"] or diff["only_pin_audit"] or diff["different_sha"])
            payload = {
                "command": "reconcile",
                "counts": {key: len(diff[key]) for key in ("different_sha", "only_pin_audit", "only_taxontoy", "same")},
                "different_sha": diff["different_sha"],
                "errors": [],
                "inputs": _inputs(input_paths),
                "ok": not differed,
                "only_pin_audit": diff["only_pin_audit"],
                "only_taxontoy": diff["only_taxontoy"],
                "repository": source or None,
                "same": diff["same"],
                "skipped": too_skipped + audit_skipped,
                "tool": "taxontoy",
                "version": __version__,
            }
            _emit(_render(payload, fmt), output)
            if differed and args.strict:
                return 2
            return 0 if payload["ok"] else 1
        graphs, types, graph_paths = _parse_graphs(args.graph)
        if args.command == "candidates":
            waves = {}
            report_errors = []
            for wave in WAVES:
                grouped = defaultdict(list)
                malformed = 0
                for edge in graphs[wave]:
                    try:
                        grouped[_relation_name(edge)].append(edge)
                    except (KeyError, ValueError):
                        malformed += 1
                rows, notes = property_candidates(data, grouped, types[wave])
                if malformed:
                    report_errors.append(f"malformed {wave}: {malformed}")
                waves[wave] = {"candidates": rows, "notes": notes}
            payload = {
                "command": "candidates",
                "errors": sorted(set(report_errors)),
                "inputs": _inputs([table_path, *graph_paths]),
                "ok": not report_errors,
                "tool": "taxontoy",
                "version": __version__,
                "waves": waves,
            }
            _emit(_render(payload, fmt), output)
            return 0 if payload["ok"] else 1
        root = _onto_root(args.onto_root)
        knowledgegraph = _load_knowledgegraph(root)
        marker = root / "knowledgegraph.py"
        waves, cover_errors = cover_waves(data, graphs, knowledgegraph, types)
        payload = {
            "command": "coverage",
            "errors": cover_errors,
            "inputs": _inputs([table_path, *graph_paths]),
            "ok": not cover_errors,
            "onto": {"path": _display_path(marker), "sha256": _sha256(marker)},
            "tool": "taxontoy",
            "version": __version__,
            "waves": waves,
        }
        _emit(_render(payload, fmt), output)
        if cover_errors and args.strict:
            return 2
        return 0 if payload["ok"] else 1
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(f"taxontoy: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
