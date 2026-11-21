"""Validate TaxOntOy's relation table and measure coverage of an OTL graph.

Paths come from arguments or from TAXONTOY_TABLE and ONTO_CHECKOUT.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

__version__ = "0.2.0"
SCHEMA_ID = "taxontoy-relations/2"
WAVES = ("audit", "concepts", "cases")
TOP_KEYS = {"schema", *WAVES, "direction", "excluded", "properties"}
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
    if not isinstance(value, str) or len(value) != 10 or value[4] != "-" or value[7] != "-":
        return False
    if not (value[:4].isdigit() and value[5:7].isdigit() and value[8:].isdigit()):
        return False
    try:
        datetime.date(int(value[:4]), int(value[5:7]), int(value[8:]))
    except ValueError:
        return False
    return True


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
        for wave in WAVES:
            values = excluded.get(wave)
            if isinstance(values, list):
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
                if key == "disjoint_with":
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
    extra = sorted(set(data) - TOP_KEYS)
    for key in missing:
        errors.append(f"missing key: {key}")
    for key in extra:
        errors.append(f"unknown key: {key}")
    if data.get("schema") != SCHEMA_ID:
        errors.append(f"schema: expected {SCHEMA_ID}")

    classified = {}
    for wave in WAVES:
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
    for key in sorted(set(excluded) - set(WAVES)):
        errors.append(f"excluded: unknown profile {key}")
    for wave in WAVES:
        names = excluded.get(wave)
        if not isinstance(names, list):
            errors.append(f"excluded.{wave}: must be an array")
            continue
        if names != sorted(set(names)):
            errors.append(f"excluded.{wave}: must be sorted and unique")
        mapping = data.get(wave) if isinstance(data.get(wave), dict) else {}
        for name in names:
            if not _name_ok(name):
                errors.append(f"excluded.{wave}: names must be non-empty strings")
            elif name in mapping:
                errors.append(f"{wave}: {name} is both classified and excluded")
    _validate_properties(data, classified, errors)
    return sorted(set(errors))


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
    for wave in WAVES:
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
        if item["detection"] == "inverse-pair":
            left, right = item["relations"]
            declared = _inverse_partner(table, left) == right and _inverse_partner(table, right) == left
        row = {"declared": declared, "detection": item["detection"], "folded": False, "relations": item["relations"]}
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
    path = Path(directory) / "knowledgegraph.py"
    if not path.is_file():
        raise ValueError(f"knowledgegraph.py is not in {directory}")
    spec = importlib.util.spec_from_file_location("taxontoy_onto_knowledgegraph", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for attr in ("relation_cycles", "topological_order"):
        if not callable(getattr(module, attr, None)):
            raise ValueError(f"{path} has no {attr}")
    return module


def _symmetric(edges):
    pairs = {(edge["source"], edge["target"]) for edge in edges}
    return all((target, source) in pairs for source, target in pairs)


def _forward_conflict(edges):
    pairs = {(edge["source"], edge["target"]) for edge in edges}
    return bool(pairs) and _symmetric(edges) and any(source != target for source, target in pairs)


def cover_waves(table, graphs, knowledgegraph):
    """Partition each wave's relation names. Candidates are reported and not folded."""
    errors = []
    waves = {}
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
        excluded = table["excluded"][wave]
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
    return waves, sorted(set(errors))


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
        lines.append(
            f"property {row['scope']} {row['name']} {row['property']}{partner} since {row['since']}"
        )
    for row in payload.get("relations", []):
        lines.append(
            f"relation {row['profile']} {row['relation']} class {row['class']} direction {row['direction']}"
        )
    for wave in payload.get("excluded", {}):
        for name in payload["excluded"][wave]:
            lines.append(f"excluded {wave} {name}")
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
    return "\n".join(lines) + "\n"


def _emit(text, output):
    if output is None:
        sys.stdout.write(text)
        return
    Path(output).write_text(text, encoding="utf-8")


def _table_path(args):
    text = _clean(args.table)
    if text:
        return text
    env = _clean(os.environ.get("TAXONTOY_TABLE"))
    if env:
        return env
    raise ValueError("pass the table path or set TAXONTOY_TABLE")


def _onto_path(args):
    text = _clean(args.onto)
    if text:
        return text
    env = _clean(os.environ.get("ONTO_CHECKOUT"))
    if env:
        return env
    raise ValueError("pass --onto or set ONTO_CHECKOUT")


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
        payload["excluded"] = {wave: list(data["excluded"][wave]) for wave in WAVES}
        payload["properties"] = _property_rows(data)
        payload["relations"] = [
            {
                "class": data[wave][relation],
                "direction": data["direction"][relation],
                "profile": wave,
                "relation": relation,
            }
            for wave in WAVES
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


def _add_format(parser, default):
    parser.add_argument("--format", choices=("text", "json"), help="text or json; json is canonical machine output")
    parser.add_argument("--json", action="store_true", help="shorthand for --format json")
    parser.add_argument("--output", type=Path, help="write the report here instead of stdout")
    parser.set_defaults(format_default=default)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="taxontoy", description=__doc__)
    parser.add_argument("--version", action="version", version=f"taxontoy {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="check the classification table")
    validate.add_argument("table", nargs="?", type=Path)
    _add_format(validate, "text")

    cover = commands.add_parser("coverage", help="partition relation names in OTL graphs")
    cover.add_argument("table", nargs="?", type=Path)
    cover.add_argument("--graph", action="append", default=[], metavar="WAVE=PATH")
    cover.add_argument("--onto", type=Path, help="ONTO checkout; overrides ONTO_CHECKOUT")
    cover.add_argument("--strict", action="store_true", help="exit 2 when coverage is not closed")
    _add_format(cover, "json")

    candidates = commands.add_parser("candidates", help="report alias and property candidates without folding")
    candidates.add_argument("table", nargs="?", type=Path)
    candidates.add_argument("--graph", action="append", default=[], metavar="WAVE=PATH")
    _add_format(candidates, "json")

    args = parser.parse_args(argv)
    try:
        fmt = _format(args)
        output = _clean(args.output) or None
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
        onto = _onto_path(args)
        knowledgegraph = _load_knowledgegraph(onto)
        waves, cover_errors = cover_waves(data, graphs, knowledgegraph)
        payload = {
            "command": "coverage",
            "errors": cover_errors,
            "inputs": _inputs([table_path, *graph_paths]),
            "ok": not cover_errors,
            "onto": {"path": f"{onto.rstrip('/')}/knowledgegraph.py", "sha256": _sha256(Path(onto) / "knowledgegraph.py")},
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
