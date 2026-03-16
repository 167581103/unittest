from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class CodeBlock:
    type: str
    signature: str
    code: str
    comment: str
    file: str
    class_name: str
    start_line: int


@dataclass
class ClassInfo:
    name: str
    file: str
    package: str
    imports: List[str]
    fields: List[Dict]
    constants: List[Dict]
    constructors: List[Dict]
    methods: List[Dict]
    super_class: Optional[str] = None
    interfaces: List[str] = field(default_factory=list)


class JavaParser:
    def __init__(self):
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_java
            self.parser = Parser()
            self.parser.language = Language(tree_sitter_java.language())
            self.available = True
        except ImportError:
            self.available = False

    def parse_file(self, path: str) -> Tuple[List[CodeBlock], Optional[ClassInfo]]:
        if not self.available:
            return [], None
        try:
            with open(path, "r", encoding="utf-8") as f:
                code = f.read()
        except OSError:
            return [], None
        tree = self.parser.parse(code.encode())
        return self._walk(path, code, tree.root_node)

    # ------------------------------------------------------------------ helpers

    def _text(self, node, code: str) -> str:
        return code[node.start_byte:node.end_byte]

    def _child_text(self, node, code: str, *types) -> Optional[str]:
        for ch in node.children:
            if ch.type in types:
                return self._text(ch, code)
        return None

    def _parse_package(self, node, code: str) -> str:
        raw = self._text(node, code)
        parts = raw.split()
        if len(parts) >= 2:
            return parts[1].rstrip(";")
        return ""

    # ------------------------------------------------------------------ walk

    def _walk(self, filepath: str, code: str, root) -> Tuple[List[CodeBlock], Optional[ClassInfo]]:
        pkg = ""
        imports = []
        cls_node = None
        for node in root.children:
            if node.type == "package_declaration":
                pkg = self._parse_package(node, code)
            elif node.type == "import_declaration":
                imports.append(self._text(node, code))
            elif node.type in ("class_declaration", "interface_declaration", "enum_declaration"):
                cls_node = node
        if cls_node is None:
            return [], None
        return self._parse_class(filepath, code, pkg, imports, cls_node)

    # ------------------------------------------------------------------ class

    def _parse_class(self, filepath: str, code: str, pkg: str, imports: List[str], node):
        name = self._child_text(node, code, "identifier") or ""
        super_class = None
        interfaces = []
        fields, constants, constructors, methods = [], [], [], []
        blocks = []

        for ch in node.children:
            if ch.type == "superclass":
                super_class = self._child_text(ch, code, "type_identifier")
            elif ch.type == "super_interfaces":
                interfaces = [self._text(x, code) for x in ch.children if x.type == "type_identifier"]
            elif ch.type in ("class_body", "enum_body", "interface_body"):
                for item in ch.children:
                    if item.type == "field_declaration":
                        fd = self._parse_field(item, code)
                        if fd["is_constant"]:
                            constants.append(fd)
                        else:
                            fields.append(fd)
                    elif item.type == "enum_constant":
                        ename = self._child_text(item, code, "identifier") or ""
                        constants.append({
                            "name": ename,
                            "signature": ename,
                            "type": "enum",
                            "modifiers": [],
                            "is_constant": True,
                        })
                    elif item.type == "constructor_declaration":
                        m = self._parse_method(item, code)
                        constructors.append(m)
                        blocks.append(CodeBlock(
                            "constructor", m["signature"], self._text(item, code),
                            "", filepath, name, item.start_point[0] + 1,
                        ))
                    elif item.type in ("method_declaration", "interface_method_declaration"):
                        m = self._parse_method(item, code)
                        methods.append(m)
                        blocks.append(CodeBlock(
                            "method", m["signature"], self._text(item, code),
                            "", filepath, name, item.start_point[0] + 1,
                        ))

        class_info = ClassInfo(name, filepath, pkg, imports, fields, constants, constructors, methods, super_class, interfaces)
        return blocks, class_info

    # ------------------------------------------------------------------ field

    def _parse_field(self, node, code: str) -> Dict:
        modifiers, typ, name = [], "", ""
        for ch in node.children:
            if ch.type == "modifiers":
                modifiers = [self._text(m, code) for m in ch.children if m.type not in ("(", ")", ",")]
            elif ch.type in ("type_identifier", "array_type", "generic_type", "integral_type", "floating_point_type"):
                typ = self._text(ch, code)
            elif ch.type == "variable_declarator":
                name = self._child_text(ch, code, "identifier") or ""
        is_constant = "static" in modifiers and "final" in modifiers
        return {
            "name": name,
            "signature": self._text(node, code).rstrip(";").strip(),
            "type": typ,
            "modifiers": modifiers,
            "is_constant": is_constant,
        }

    # ------------------------------------------------------------------ method

    def _parse_param_type(self, param_node, code: str) -> str:
        for ch in param_node.children:
            if ch.type in ("type_identifier", "array_type", "generic_type", "integral_type",
                           "floating_point_type", "boolean_type", "void_type"):
                return self._text(ch, code)
        return self._text(param_node, code)

    def _parse_method(self, node, code: str) -> Dict:
        modifiers, return_type, name, params = [], "", "", []
        for ch in node.children:
            if ch.type == "modifiers":
                modifiers = [self._text(m, code) for m in ch.children if m.type not in ("(", ")", ",")]
            elif ch.type == "void_type":
                return_type = "void"
            elif ch.type in ("type_identifier", "array_type", "generic_type", "integral_type", "floating_point_type"):
                return_type = self._text(ch, code)
            elif ch.type == "identifier":
                name = self._text(ch, code)
            elif ch.type == "formal_parameters":
                params = [
                    self._text(p, code)
                    for p in ch.children
                    if p.type in ("formal_parameter", "spread_parameter")
                ]
        mod_str = " ".join(modifiers)
        param_str = ", ".join(params)
        signature = f"{mod_str} {return_type} {name}({param_str})".strip()
        param_types = [self._parse_param_type(p, code) for p in node.children
                       if p.type == "formal_parameters"
                       for p in p.children
                       if p.type in ("formal_parameter", "spread_parameter")]
        return {
            "signature": signature,
            "name": name,
            "return_type": return_type,
            "params": params,
            "param_types": param_types,
            "modifiers": modifiers,
        }
