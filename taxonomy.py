"""Validate the OTL relation classification table and report coverage against a graph."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

DEFAULT_TABLE = Path(__file__).resolve().parent / "otl-relations.json"


def edge_relation(edge):
    return edge["kind"] if "kind" in edge else edge["relation"]


def validate_table(data):
    """Return findings; an empty list means the table is well-formed."""
    findings = []
    if not isinstance(data, dict):
        return [{"kind": "shape", "detail": "the table must be a JSON object of profiles"}]
    if not data:
        findings.append({"kind": "empty", "detail": "the table has no profiles"})
        return findings
    for profile, mapping in data.items():
        if not isinstance(profile, str) or not profile.strip():
            findings.append({"kind": "profile", "detail": "profile names must be non-empty strings"})
            continue
        if not isinstance(mapping, dict):
            findings.append({"kind": "shape", "profile": profile, "detail": "each profile must be an object"})
            continue
        if not mapping:
            findings.append({"kind": "empty", "profile": profile, "detail": "profile has no relations"})
        for relation, category in mapping.items():
            if not isinstance(relation, str) or not relation.strip():
                findings.append({"kind": "relation", "profile": profile,
                                 "detail": "relation names must be non-empty strings"})
            if not isinstance(category, str) or not category.strip():
                findings.append({"kind": "class", "profile": profile, "relation": relation,
                                 "detail": "class names must be non-empty strings"})
    return findings


def load_table(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    findings = validate_table(data)
    return data, findings


def coverage(graph, classes):
    """Compare a profile mapping against the relations actually used in ``graph``."""
    counts = Counter()
    malformed = 0
    for index, edge in enumerate(graph["edges"]):
        try:
            relation = edge_relation(edge)
        except KeyError:
            malformed += 1
            continue
        if not isinstance(relation, str) or not relation:
            malformed += 1
            continue
        counts[relation] += 1
    mapped, unmapped = [], []
    for relation, n in sorted(counts.items()):
        if relation in classes:
            mapped.append({"relation": relation, "class": classes[relation], "edges": n})
        else:
            unmapped.append({"relation": relation, "edges": n})
    unused = sorted(relation for relation in classes if relation not in counts)
    return {
        "mapped": mapped,
        "unmapped": unmapped,
        "unused_in_graph": unused,
        "edge_count": sum(counts.values()),
        "malformed_edges": malformed,
    }


def render_validate(data, findings, *, fmt):
    if fmt == "json":
        return json.dumps({"ok": not findings, "profiles": len(data) if isinstance(data, dict) else 0,
                           "findings": findings}, ensure_ascii=False, indent=2) + "\n"
    if findings:
        lines = [f"invalid: {len(findings)} finding(s)"]
        for item in findings:
            where = item.get("profile", "")
            rel = item.get("relation", "")
            prefix = ":".join(part for part in (where, str(rel) if rel != "" else "") if part)
            lines.append(f"  {item['kind']}" + (f" ({prefix})" if prefix else "") + f": {item['detail']}")
        return "\n".join(lines) + "\n"
    relations = sum(len(mapping) for mapping in data.values())
    lines = [f"ok: {len(data)} profiles, {relations} relations"]
    for profile, mapping in data.items():
        pairs = ", ".join(f"{rel}→{cls}" for rel, cls in mapping.items())
        lines.append(f"  {profile}: {len(mapping)} ({pairs})")
    return "\n".join(lines) + "\n"


def render_coverage(report, profile, *, fmt):
    payload = {"profile": profile, **report}
    if fmt == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    lines = [
        f"profile: {profile}",
        f"edges: {report['edge_count']}",
        f"malformed_edges: {report['malformed_edges']}",
        "mapped:",
    ]
    if report["mapped"]:
        lines.extend(f"  {row['relation']} → {row['class']}  ({row['edges']})" for row in report["mapped"])
    else:
        lines.append("  (none)")
    lines.append("unmapped:")
    if report["unmapped"]:
        lines.extend(f"  {row['relation']}  ({row['edges']})" for row in report["unmapped"])
    else:
        lines.append("  (none)")
    lines.append("unused in table:")
    if report["unused_in_graph"]:
        lines.extend(f"  {name}" for name in report["unused_in_graph"])
    else:
        lines.append("  (none)")
    return "\n".join(lines) + "\n"


def _add_format(parser, default="text"):
    parser.add_argument("--format", choices=("text", "json"), default=default,
                        help="text renders a human document; json writes one JSON document")
    parser.add_argument("--json", action="store_const", const="json", dest="format",
                        help="shorthand for --format json")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)

    validate = subs.add_parser("validate", help="check the classification table shape")
    validate.add_argument("table", nargs="?", type=Path, default=DEFAULT_TABLE)
    _add_format(validate)

    cover = subs.add_parser("coverage", help="compare a graph's relations against one profile")
    cover.add_argument("graph", type=Path)
    cover.add_argument("table", type=Path)
    cover.add_argument("profile")
    cover.add_argument("--strict", action="store_true",
                       help="exit 2 when the graph has unmapped or malformed edges")
    _add_format(cover, default="json")

    args = parser.parse_args(argv)
    try:
        table, findings = load_table(args.table)
    except (OSError, json.JSONDecodeError) as error:
        print(error, file=sys.stderr)
        return 1

    if args.command == "validate":
        sys.stdout.write(render_validate(table, findings, fmt=args.format))
        return 1 if findings else 0

    if findings:
        sys.stdout.write(render_validate(table, findings, fmt=args.format))
        return 1
    if args.profile not in table:
        print(f"unknown profile {args.profile!r}; known: {', '.join(table)}", file=sys.stderr)
        return 1
    try:
        graph = json.loads(args.graph.read_text(encoding="utf-8"))
        if not isinstance(graph, dict) or "edges" not in graph:
            raise ValueError("graph JSON must be an object with an edges array")
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    report = coverage(graph, table[args.profile])
    sys.stdout.write(render_coverage(report, args.profile, fmt=args.format))
    if args.strict and (report["unmapped"] or report["malformed_edges"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
