"""
Parse a source file using Tree-sitter and extract:
  - Symbol definitions (functions, classes, methods)
  - CALLS edges (function call relationships)
  - IMPORTS edges (import/require statements)

Returns a ParseResult dataclass — no DB dependency.

─── Adding a new language ─────────────────────────────────────────────────────
Option A — Config-based (no code needed):
  Edit custom_languages.json in this directory:
  {
    "solidity": {
      "extensions": [".sol"],
      "tree_sitter_lang": "solidity",
      "function_node_types": ["function_definition"],
      "class_node_types":    ["contract_definition"],
      "call_node_types":     ["call_expression"],
      "import_node_types":   ["import_directive"],
      "name_field":          "name"
    }
  }

Option B — Full custom parser (for complex cases):
  Drop a file named lang_<name>.py next to parser.py with:
    def parse(root_node, source: bytes) -> ParseResult: ...
  Then register the extension in custom_languages.json with
  "custom_module": "lang_solidity"  (no .py)
────────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import importlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from tree_sitter_languages import get_parser as _get_ts_parser
except ImportError:
    raise ImportError("Run: pip install tree-sitter-languages")


HERE = Path(__file__).parent
CUSTOM_LANGUAGES_FILE = HERE / "custom_languages.json"


# ── Result types ──────────────────────────────────────────────────────

@dataclass
class Symbol:
    name: str
    kind: str          # function | class | method | struct | interface
    line_start: int
    line_end: int
    parent: str = None


@dataclass
class CallEdge:
    caller: str
    callee: str
    line: int


@dataclass
class ImportEdge:
    target: str
    line: int


@dataclass
class ParseResult:
    symbols: list[Symbol] = field(default_factory=list)
    calls:   list[CallEdge] = field(default_factory=list)
    imports: list[ImportEdge] = field(default_factory=list)


# ── Built-in extension → tree-sitter language map ─────────────────────

_BUILTIN_EXT_MAP: dict[str, str] = {
    ".py":   "python",
    ".js":   "javascript",
    ".jsx":  "javascript",
    ".ts":   "typescript",
    ".tsx":  "tsx",
    ".go":   "go",
    ".php":  "php",
    ".java": "java",
    ".rb":   "ruby",
    ".rs":   "rust",
    ".c":    "c",
    ".cpp":  "cpp",
    ".h":    "c",
    ".hpp":  "cpp",
}

# Merged map (built-in + custom), populated on first use
_ext_map: dict[str, str] | None = None
# custom language configs loaded from custom_languages.json
_custom_configs: dict[str, dict] = {}


def _load_custom():
    global _ext_map, _custom_configs
    if _ext_map is not None:
        return
    _ext_map = dict(_BUILTIN_EXT_MAP)
    _custom_configs = {}
    if CUSTOM_LANGUAGES_FILE.exists():
        try:
            data = json.loads(CUSTOM_LANGUAGES_FILE.read_text(encoding="utf-8"))
            for lang_name, cfg in data.items():
                if not isinstance(cfg, dict) or lang_name.startswith("_"):
                    continue  # skip comments / example entries
                _custom_configs[lang_name] = cfg
                for ext in cfg.get("extensions", []):
                    _ext_map[ext] = lang_name
        except Exception as e:
            print(f"[parser] Warning: could not load {CUSTOM_LANGUAGES_FILE}: {e}")


def detect_language(file_path: str) -> str | None:
    _load_custom()
    ext = os.path.splitext(file_path)[1].lower()
    return _ext_map.get(ext)


def parse_file(file_path: str) -> ParseResult | None:
    lang = detect_language(file_path)
    if not lang:
        return None
    try:
        source = open(file_path, "rb").read()
    except (OSError, PermissionError):
        return None

    # resolve tree-sitter language name (custom may override)
    cfg = _custom_configs.get(lang, {})
    ts_lang = cfg.get("tree_sitter_lang", lang)

    try:
        parser = _get_ts_parser(ts_lang)
        tree = parser.parse(source)
    except Exception:
        return None

    root = tree.root_node

    # custom module (Option B)
    if "custom_module" in cfg:
        try:
            mod = importlib.import_module(cfg["custom_module"])
            return mod.parse(root, source)
        except Exception as e:
            print(f"[parser] Warning: custom module error for {lang}: {e}")
            return ParseResult()

    # config-based generic parser (Option A) for custom languages
    if lang in _custom_configs:
        return _parse_from_config(root, source, cfg)

    # built-in parsers
    if lang == "python":
        return _parse_python(root, source)
    if lang in ("javascript", "typescript", "tsx"):
        return _parse_js_ts(root, source)
    if lang == "go":
        return _parse_go(root, source)
    if lang == "php":
        return _parse_php(root, source)

    return _parse_generic(root, source, lang)


# ── Helpers ───────────────────────────────────────────────────────────

def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_all(node, *types):
    if node.type in types:
        yield node
    for child in node.children:
        yield from _find_all(child, *types)


def _enclosing_function(node, source: bytes, extra_types: tuple = ()) -> str | None:
    func_types = {
        "function_definition", "function_declaration", "method_declaration",
        "method_definition", "arrow_function", "func_literal",
    } | set(extra_types)
    current = node.parent
    while current:
        if current.type in func_types:
            name_node = current.child_by_field_name("name")
            if name_node:
                return _text(name_node, source)
        current = current.parent
    return None


# ── Config-based generic parser (Option A) ────────────────────────────

def _parse_from_config(root, source: bytes, cfg: dict) -> ParseResult:
    result = ParseResult()
    name_field = cfg.get("name_field", "name")

    for nt in cfg.get("function_node_types", []):
        for node in _find_all(root, nt):
            nn = node.child_by_field_name(name_field)
            if nn:
                result.symbols.append(Symbol(
                    name=_text(nn, source), kind="function",
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                ))

    for nt in cfg.get("class_node_types", []):
        for node in _find_all(root, nt):
            nn = node.child_by_field_name(name_field)
            if nn:
                result.symbols.append(Symbol(
                    name=_text(nn, source), kind="class",
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                ))

    for nt in cfg.get("call_node_types", []):
        for node in _find_all(root, nt):
            fn = node.child_by_field_name("function") or node.child_by_field_name("name")
            if fn:
                callee = _text(fn, source).split(".")[-1]
                caller = _enclosing_function(node, source) or "<module>"
                result.calls.append(CallEdge(caller=caller, callee=callee,
                                             line=node.start_point[0] + 1))

    for nt in cfg.get("import_node_types", []):
        for node in _find_all(root, nt):
            result.imports.append(ImportEdge(
                target=_text(node, source).strip(),
                line=node.start_point[0] + 1,
            ))

    return result


# ── Python ────────────────────────────────────────────────────────────

def _parse_python(root, source: bytes) -> ParseResult:
    result = ParseResult()
    _python_symbols(root, source, result, parent_class=None)
    _python_imports(root, source, result)
    _python_calls(root, source, result)
    return result


def _python_symbols(node, source, result, parent_class):
    for child in node.children:
        if child.type == "decorated_definition":
            for inner in child.children:
                if inner.type in ("function_definition", "class_definition"):
                    _python_symbols(inner, source, result, parent_class)
            continue
        if child.type == "function_definition":
            nn = child.child_by_field_name("name")
            if nn:
                name = _text(nn, source)
                result.symbols.append(Symbol(
                    name=name, kind="method" if parent_class else "function",
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    parent=parent_class,
                ))
                body = child.child_by_field_name("body")
                if body:
                    _python_symbols(body, source, result, None)
        elif child.type == "class_definition":
            nn = child.child_by_field_name("name")
            if nn:
                name = _text(nn, source)
                result.symbols.append(Symbol(
                    name=name, kind="class",
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                ))
                body = child.child_by_field_name("body")
                if body:
                    _python_symbols(body, source, result, name)
        else:
            _python_symbols(child, source, result, parent_class)


def _python_imports(root, source, result):
    for node in _find_all(root, "import_statement", "import_from_statement"):
        line = node.start_point[0] + 1
        if node.type == "import_statement":
            for child in node.children:
                if child.type in ("dotted_name", "aliased_import"):
                    result.imports.append(ImportEdge(
                        target=_text(child, source).split(" as ")[0].strip(),
                        line=line,
                    ))
        else:
            module = node.child_by_field_name("module_name")
            if module:
                result.imports.append(ImportEdge(target=_text(module, source), line=line))


def _python_calls(root, source, result):
    for node in _find_all(root, "call"):
        fn = node.child_by_field_name("function")
        if fn:
            callee = _text(fn, source).split(".")[-1]
            caller = _enclosing_function(node, source) or "<module>"
            result.calls.append(CallEdge(caller=caller, callee=callee,
                                         line=node.start_point[0] + 1))


# ── JS / TS ───────────────────────────────────────────────────────────

def _parse_js_ts(root, source: bytes) -> ParseResult:
    result = ParseResult()
    _js_symbols(root, source, result, None)
    _js_imports(root, source, result)
    _js_calls(root, source, result)
    return result


def _js_symbols(node, source, result, parent_class):
    for child in node.children:
        if child.type == "function_declaration":
            nn = child.child_by_field_name("name")
            if nn:
                result.symbols.append(Symbol(
                    name=_text(nn, source),
                    kind="method" if parent_class else "function",
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    parent=parent_class,
                ))
                body = child.child_by_field_name("body")
                if body:
                    _js_symbols(body, source, result, None)
        elif child.type in ("class_declaration", "class"):
            nn = child.child_by_field_name("name")
            name = _text(nn, source) if nn else "<anonymous>"
            result.symbols.append(Symbol(
                name=name, kind="class",
                line_start=child.start_point[0] + 1,
                line_end=child.end_point[0] + 1,
            ))
            body = child.child_by_field_name("body")
            if body:
                _js_symbols(body, source, result, name)
        elif child.type == "method_definition":
            nn = child.child_by_field_name("name")
            if nn:
                result.symbols.append(Symbol(
                    name=_text(nn, source), kind="method",
                    line_start=child.start_point[0] + 1,
                    line_end=child.end_point[0] + 1,
                    parent=parent_class,
                ))
        elif child.type in ("lexical_declaration", "variable_declaration"):
            for decl in _find_all(child, "variable_declarator"):
                nn = decl.child_by_field_name("name")
                val = decl.child_by_field_name("value")
                if nn and val and val.type in ("arrow_function", "function"):
                    result.symbols.append(Symbol(
                        name=_text(nn, source),
                        kind="method" if parent_class else "function",
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        parent=parent_class,
                    ))
        else:
            _js_symbols(child, source, result, parent_class)


def _js_imports(root, source, result):
    for node in _find_all(root, "import_statement", "call_expression"):
        line = node.start_point[0] + 1
        if node.type == "import_statement":
            src = node.child_by_field_name("source")
            if src:
                result.imports.append(ImportEdge(
                    target=_text(src, source).strip("'\""), line=line))
        elif node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn and _text(fn, source) == "require":
                args = node.child_by_field_name("arguments")
                if args and args.child_count > 1:
                    result.imports.append(ImportEdge(
                        target=_text(args.children[1], source).strip("'\""),
                        line=line,
                    ))


def _js_calls(root, source, result):
    for node in _find_all(root, "call_expression"):
        fn = node.child_by_field_name("function")
        if not fn:
            continue
        raw = _text(fn, source)
        if raw == "require":
            continue
        caller = _enclosing_function(node, source) or "<module>"
        result.calls.append(CallEdge(
            caller=caller, callee=raw.split(".")[-1],
            line=node.start_point[0] + 1,
        ))


# ── Go ────────────────────────────────────────────────────────────────

def _parse_go(root, source: bytes) -> ParseResult:
    result = ParseResult()

    # functions
    for node in _find_all(root, "function_declaration"):
        nn = node.child_by_field_name("name")
        if nn:
            result.symbols.append(Symbol(
                name=_text(nn, source), kind="function",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
            ))

    # methods  (func (r Receiver) MethodName(...))
    for node in _find_all(root, "method_declaration"):
        nn = node.child_by_field_name("name")
        if nn:
            result.symbols.append(Symbol(
                name=_text(nn, source), kind="method",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
            ))

    # structs & interfaces
    for node in _find_all(root, "type_declaration"):
        for spec in _find_all(node, "type_spec"):
            nn = spec.child_by_field_name("name")
            type_node = spec.child_by_field_name("type")
            if nn and type_node:
                kind = "interface" if type_node.type == "interface_type" else "struct"
                result.symbols.append(Symbol(
                    name=_text(nn, source), kind=kind,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                ))

    # imports
    for node in _find_all(root, "import_spec"):
        path_node = node.child_by_field_name("path")
        if path_node:
            result.imports.append(ImportEdge(
                target=_text(path_node, source).strip('"'),
                line=node.start_point[0] + 1,
            ))

    # calls
    for node in _find_all(root, "call_expression"):
        fn = node.child_by_field_name("function")
        if fn:
            raw = _text(fn, source)
            callee = raw.split(".")[-1]
            caller = _enclosing_function(node, source,
                                         extra_types=("method_declaration",)) or "<package>"
            result.calls.append(CallEdge(caller=caller, callee=callee,
                                         line=node.start_point[0] + 1))

    return result


# ── PHP ───────────────────────────────────────────────────────────────

def _parse_php(root, source: bytes) -> ParseResult:
    result = ParseResult()

    # functions
    for node in _find_all(root, "function_definition"):
        nn = node.child_by_field_name("name")
        if nn:
            result.symbols.append(Symbol(
                name=_text(nn, source), kind="function",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
            ))

    # classes & interfaces & traits
    for node in _find_all(root, "class_declaration",
                           "interface_declaration", "trait_declaration"):
        nn = node.child_by_field_name("name")
        if nn:
            kind = {"class_declaration": "class",
                    "interface_declaration": "interface",
                    "trait_declaration": "trait"}.get(node.type, "class")
            result.symbols.append(Symbol(
                name=_text(nn, source), kind=kind,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
            ))

    # methods inside classes
    for node in _find_all(root, "method_declaration"):
        nn = node.child_by_field_name("name")
        if nn:
            result.symbols.append(Symbol(
                name=_text(nn, source), kind="method",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
            ))

    # imports: use, require, include
    for node in _find_all(root, "namespace_use_declaration"):
        for clause in _find_all(node, "namespace_use_clause"):
            result.imports.append(ImportEdge(
                target=_text(clause, source).strip(),
                line=node.start_point[0] + 1,
            ))
    for node in _find_all(root, "require_expression", "require_once_expression",
                           "include_expression", "include_once_expression"):
        for child in node.children:
            if child.type in ("string", "encapsed_string"):
                result.imports.append(ImportEdge(
                    target=_text(child, source).strip("'\""),
                    line=node.start_point[0] + 1,
                ))

    # calls: foo(), $obj->method(), Class::method()
    for node in _find_all(root, "function_call_expression",
                           "member_call_expression",
                           "scoped_call_expression",
                           "nullsafe_member_call_expression"):
        if node.type == "function_call_expression":
            nn = node.child_by_field_name("name")
            callee = _text(nn, source) if nn else "?"
        elif node.type == "scoped_call_expression":
            # Class::method() — second `name` child is the method
            name_nodes = [c for c in node.children if c.type == "name"]
            callee = _text(name_nodes[-1], source) if name_nodes else "?"
        else:
            nn = node.child_by_field_name("name")
            callee = _text(nn, source) if nn else "?"
        caller = _enclosing_function(node, source,
                                     extra_types=("method_declaration",)) or "<global>"
        result.calls.append(CallEdge(caller=caller, callee=callee,
                                     line=node.start_point[0] + 1))

    return result


# ── Generic fallback ──────────────────────────────────────────────────

_GENERIC_FUNC_TYPES: dict[str, list[str]] = {
    "java":  ["method_declaration", "constructor_declaration"],
    "rust":  ["function_item"],
    "ruby":  ["method", "singleton_method"],
    "c":     ["function_definition"],
    "cpp":   ["function_definition"],
}
_GENERIC_CLASS_TYPES: dict[str, list[str]] = {
    "java": ["class_declaration", "interface_declaration"],
    "rust": ["struct_item", "impl_item"],
}


def _parse_generic(root, source: bytes, lang: str) -> ParseResult:
    result = ParseResult()
    for ft in _GENERIC_FUNC_TYPES.get(lang, []):
        for node in _find_all(root, ft):
            nn = node.child_by_field_name("name")
            if nn:
                result.symbols.append(Symbol(
                    name=_text(nn, source), kind="function",
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                ))
    for ct in _GENERIC_CLASS_TYPES.get(lang, []):
        for node in _find_all(root, ct):
            nn = node.child_by_field_name("name")
            if nn:
                result.symbols.append(Symbol(
                    name=_text(nn, source), kind="class",
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                ))
    return result
