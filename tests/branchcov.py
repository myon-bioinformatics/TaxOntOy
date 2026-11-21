"""Line and branch tracing for taxontoy.py.

The suite installs this tracer in-process and in CLI children. Branch arcs are
the two ways out of each conditional jump. Python 3.12+ leaves END_FOR on the
exhausted FOR_ITER target; the tracer does not report that opcode, so the arc
lands on the following instruction.
"""
import dis
import hashlib
import json
import os
import sys
from pathlib import Path

_SKIP_TARGETS = {"END_FOR"}
_LINES = set()
_ARCS = set()
_PREV = {}
_CONDS = {}
_TARGET_SUFFIX = f"{os.sep}taxontoy.py"


def _conditional(opname):
    if opname == "FOR_ITER":
        return True
    return "JUMP" in opname and "IF" in opname


def code_key(code):
    digest = hashlib.sha256(code.co_code).hexdigest()[:16]
    return (code.co_qualname, code.co_firstlineno, digest)


def _jump_aliases(instructions):
    """Map the traced offset of a jump to the jump opcode.

    3.11 reports the EXTENDED_ARG prefix. 3.13+ reports the jump itself.
    """
    alias = {}
    for index, inst in enumerate(instructions):
        if not _conditional(inst.opname):
            continue
        alias[inst.offset] = inst.offset
        cursor = index
        while cursor > 0 and instructions[cursor - 1].opname == "EXTENDED_ARG":
            cursor -= 1
            alias[instructions[cursor].offset] = inst.offset
    return alias


def _arcs_of(code):
    instructions = list(dis.get_instructions(code))
    index_by_offset = {inst.offset: index for index, inst in enumerate(instructions)}

    def resolve(offset):
        index = index_by_offset[offset]
        while instructions[index].opname in _SKIP_TARGETS:
            index += 1
        return instructions[index].offset

    arcs = set()
    for index, inst in enumerate(instructions):
        if not _conditional(inst.opname):
            continue
        target = inst.argval if isinstance(inst.argval, int) else getattr(inst, "jump_target", None)
        if not isinstance(target, int):
            continue
        arcs.add((inst.offset, resolve(target)))
        arcs.add((inst.offset, resolve(instructions[index + 1].offset)))
    return arcs


def static_branches(path):
    source = Path(path).read_text(encoding="utf-8")
    module = compile(source, str(Path(path).resolve()), "exec")
    found = {}

    def walk(code):
        key = code_key(code)
        arcs = _arcs_of(code)
        previous = found.get(key)
        if previous is not None and previous != arcs:
            raise RuntimeError(f"branch key collision for {key[0]}:{key[1]}")
        found[key] = arcs
        for const in code.co_consts:
            if hasattr(const, "co_code"):
                walk(const)

    walk(module)
    return found


def _line(code, offset):
    line = code.co_firstlineno
    for start, end, lineno in code.co_lines():
        if lineno is not None and start <= offset:
            line = lineno
        if end is not None and offset < end and lineno is not None:
            return lineno
    return line


def explain(path, key, src, dst):
    source = Path(path).read_text(encoding="utf-8")
    module = compile(source, str(Path(path).resolve()), "exec")
    lines = source.splitlines()

    def walk(code):
        if code_key(code) == key:
            return code
        for const in code.co_consts:
            if hasattr(const, "co_code"):
                found = walk(const)
                if found is not None:
                    return found
        return None

    code = walk(module)
    if code is None:
        return f"{key[0]}:{key[1]} {src}->{dst}"
    start = _line(code, src)
    end = _line(code, dst)
    preview = lines[start - 1].strip() if 1 <= start <= len(lines) else ""
    return f"{key[0]}:{start}->{end} {preview} ({src}->{dst})"


def seen_lines():
    return set(_LINES)


def seen_arcs():
    return set(_ARCS)


def _hook(frame, event, arg):
    code = frame.f_code
    if not code.co_filename.endswith(_TARGET_SUFFIX):
        return _hook
    frame.f_trace_opcodes = True
    if event == "line":
        _LINES.add(frame.f_lineno)
    elif event == "opcode":
        offset = frame.f_lasti
        conds = _CONDS.get(id(code))
        if conds is None:
            conds = _jump_aliases(list(dis.get_instructions(code)))
            _CONDS[id(code)] = conds
        previous = _PREV.get(id(frame))
        source = conds.get(previous)
        if source is not None:
            _ARCS.add((code_key(code), source, offset))
        _PREV[id(frame)] = offset
    elif event in ("return", "exception"):
        if event == "return":
            _PREV.pop(id(frame), None)
    return _hook


def install():
    sys.settrace(_hook)


def dump():
    arcs = [
        [key[0], key[1], key[2], src, dst]
        for key, src, dst in sorted(_ARCS, key=lambda item: (item[0], item[1], item[2]))
    ]
    return json.dumps({"arcs": arcs, "lines": sorted(_LINES)}, separators=(",", ":"))


def load(text):
    payload = json.loads(text)
    lines = set(payload["lines"])
    arcs = set()
    for qualname, first, digest, src, dst in payload["arcs"]:
        arcs.add(((qualname, first, digest), src, dst))
    return lines, arcs
