"""Enclosing-function lookup. Tree-sitter when it loads, regex otherwise."""

from __future__ import annotations

import re

_PARSER = None
_PARSER_TRIED = False


def _parser():
    global _PARSER, _PARSER_TRIED
    if _PARSER_TRIED:
        return _PARSER
    _PARSER_TRIED = True
    try:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser

        _PARSER = Parser(Language(tspython.language()))
    except Exception:
        _PARSER = None
    return _PARSER


def enclosing_function(source: str, line: int) -> str | None:
    parser = _parser()
    if parser is not None:
        try:
            return _from_tree(parser, source, line)
        except Exception:
            return _from_regex(source, line)
    return _from_regex(source, line)


def _from_tree(parser, source: str, line: int) -> str | None:
    encoded = source.encode()
    tree = parser.parse(encoded)
    point = (max(line - 1, 0), 0)
    node = tree.root_node.descendant_for_point_range(point, point)
    while node is not None:
        if node.type == "function_definition":
            name = node.child_by_field_name("name")
            if name is None:
                return None
            return encoded[name.start_byte : name.end_byte].decode()
        node = node.parent
    return None


def _from_regex(source: str, line: int) -> str | None:
    current = None
    for index, text in enumerate(source.splitlines(), start=1):
        match = re.match(r"\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", text)
        if match:
            current = match.group(1)
        if index == line:
            return current
    return current


def defined_functions(source: str) -> list[str]:
    return re.findall(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", source, re.M)
