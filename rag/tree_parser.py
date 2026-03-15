"""
Java代码解析器 - 使用tree-sitter
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class CodeBlock:
    """代码块"""
    type: str  # method, field, constant, constructor
    signature: str
    code: str
    comment: str
    file: str
    class_name: str
    start_line: int


@dataclass
class ClassInfo:
    """类信息"""
    name: str
    file: str
    package: str
    imports: List[str]
    fields: List[Dict]
    constants: List[Dict]
    constructors: List[Dict]
    methods: List[Dict]
    super_class: Optional[str] = None
    interfaces: List[str] = None
    
    def __post_init__(self):
        if self.interfaces is None:
            self.interfaces = []


class JavaParser:
    """Java代码解析器"""
    
    def __init__(self):
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_java as tsjava
            self.parser = Parser(Language(tsjava.language()))
            self.available = True
        except ImportError:
            self.available = False
    
    def parse_file(self, file_path: str) -> Tuple[List[CodeBlock], Optional[ClassInfo]]:
        """解析Java文件，返回代码块和类信息"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except:
            return [], None
        
        if not self.available:
            return [], None
        
        tree = self.parser.parse(bytes(content, 'utf8'))
        return self._extract_from_tree(file_path, content, tree.root_node)
    
    def _extract_from_tree(self, file_path: str, content: str, root) -> Tuple[List[CodeBlock], Optional[ClassInfo]]:
        """从AST树提取信息"""
        blocks = []
        class_info = None
        
        # 提取包名
        package = self._get_package(root, content)
        
        # 提取导入
        imports = self._get_imports(root, content)
        
        # 查找类定义
        for node in root.children:
            if node.type == 'class_declaration' or node.type == 'interface_declaration' or node.type == 'enum_declaration':
                class_info = self._parse_class(node, content, file_path, package, imports)
                blocks.extend(self._extract_blocks(node, content, file_path, class_info.name))
                break
        
        return blocks, class_info
    
    def _get_package(self, root, content: str) -> str:
        """提取包名"""
        for node in root.children:
            if node.type == 'package_declaration':
                return self._get_text(node, content).replace('package ', '').replace(';', '').strip()
        return ''
    
    def _get_imports(self, root, content: str) -> List[str]:
        """提取导入语句"""
        imports = []
        for node in root.children:
            if node.type == 'import_declaration':
                imports.append(self._get_text(node, content))
        return imports
    
    def _parse_class(self, node, content: str, file_path: str, package: str, imports: List[str]) -> ClassInfo:
        """解析类定义"""
        class_name = ''
        for child in node.children:
            if child.type == 'identifier':
                class_name = self._get_text(child, content)
                break
        
        fields = []
        constants = []
        constructors = []
        methods = []
        super_class = None
        interfaces = []
        
        # 解析类体（class 或 enum）
        for child in node.children:
            if child.type == 'class_body':
                for item in child.children:
                    if item.type == 'field_declaration':
                        field_info = self._parse_field(item, content)
                        if field_info.get('is_constant'):
                            constants.append(field_info)
                        else:
                            fields.append(field_info)
                    elif item.type == 'constructor_declaration':
                        constructors.append(self._parse_method(item, content))
                    elif item.type == 'method_declaration':
                        methods.append(self._parse_method(item, content))
            elif child.type == 'enum_body':
                # 解析 enum 常量
                for item in child.children:
                    if item.type == 'enum_constant':
                        const_info = self._parse_enum_constant(item, content)
                        if const_info:
                            constants.append(const_info)
                    elif item.type == 'field_declaration':
                        field_info = self._parse_field(item, content)
                        if field_info.get('is_constant'):
                            constants.append(field_info)
                        else:
                            fields.append(field_info)
                    elif item.type == 'method_declaration':
                        methods.append(self._parse_method(item, content))
            elif child.type == 'superclass':
                for sub in child.children:
                    if sub.type == 'type_identifier':
                        super_class = self._get_text(sub, content)
            elif child.type == 'interfaces':
                for sub in child.children:
                    if sub.type == 'type_list':
                        for type_item in sub.children:
                            if type_item.type == 'type_identifier':
                                interfaces.append(self._get_text(type_item, content))
        
        return ClassInfo(
            name=class_name,
            file=file_path,
            package=package,
            imports=imports,
            fields=fields,
            constants=constants,
            constructors=constructors,
            methods=methods,
            super_class=super_class,
            interfaces=interfaces
        )
    
    def _parse_field(self, node, content: str) -> Dict:
        """解析字段"""
        modifiers = []
        field_type = ''
        field_name = ''
        
        for child in node.children:
            if child.type == 'modifiers':
                modifiers = [self._get_text(m, content) for m in child.children]
            elif child.type == 'type_identifier':
                field_type = self._get_text(child, content)
            elif child.type == 'variable_declarator':
                for sub in child.children:
                    if sub.type == 'identifier':
                        field_name = self._get_text(sub, content)
        
        is_constant = 'static' in modifiers and 'final' in modifiers
        
        return {
            'signature': f"{' '.join(modifiers)} {field_type} {field_name}" if modifiers else f"{field_type} {field_name}",
            'name': field_name,
            'type': field_type,
            'modifiers': modifiers,
            'is_constant': is_constant
        }
    
    def _parse_enum_constant(self, node, content: str) -> Dict:
        """解析枚举常量"""
        name = ''
        for child in node.children:
            if child.type == 'identifier':
                name = self._get_text(child, content)
                break
        
        if not name:
            return None
        
        return {
            'signature': name,
            'name': name,
            'type': 'enum_constant',
            'modifiers': ['public', 'static', 'final'],
            'is_constant': True
        }
    
    def _parse_method(self, node, content: str) -> Dict:
        """解析方法或构造函数"""
        modifiers = []
        return_type = ''
        method_name = ''
        params = []
        
        for child in node.children:
            if child.type == 'modifiers':
                modifiers = [self._get_text(m, content) for m in child.children]
            elif child.type == 'type_identifier':
                return_type = self._get_text(child, content)
            elif child.type == 'void_type':
                return_type = 'void'
            elif child.type == 'identifier':
                method_name = self._get_text(child, content)
            elif child.type == 'formal_parameters':
                params = self._parse_params(child, content)
        
        signature = f"{' '.join(modifiers)} " if modifiers else ""
        signature += f"{return_type} " if return_type else ""
        signature += f"{method_name}({', '.join(params)})"
        
        return {
            'signature': signature,
            'name': method_name,
            'return_type': return_type,
            'params': params,
            'modifiers': modifiers
        }
    
    def _parse_params(self, node, content: str) -> List[str]:
        """解析参数列表"""
        params = []
        for child in node.children:
            if child.type == 'formal_parameter':
                param_parts = []
                for sub in child.children:
                    if sub.type in ['type_identifier', 'array_type', 'generic_type']:
                        param_parts.append(self._get_text(sub, content))
                    elif sub.type == 'identifier':
                        param_parts.append(self._get_text(sub, content))
                if param_parts:
                    params.append(' '.join(param_parts))
        return params
    
    def _extract_blocks(self, class_node, content: str, file_path: str, class_name: str) -> List[CodeBlock]:
        """提取代码块"""
        blocks = []
        
        for child in class_node.children:
            if child.type == 'class_body':
                for item in child.children:
                    if item.type == 'method_declaration':
                        block = self._create_block(item, content, file_path, class_name, 'method')
                        if block:
                            blocks.append(block)
                    elif item.type == 'constructor_declaration':
                        block = self._create_block(item, content, file_path, class_name, 'constructor')
                        if block:
                            blocks.append(block)
        
        return blocks
    
    def _create_block(self, node, content: str, file_path: str, class_name: str, block_type: str) -> Optional[CodeBlock]:
        """创建代码块"""
        signature = ''
        for child in node.children:
            if child.type == 'identifier':
                signature = self._get_text(child, content)
                break
        
        if not signature:
            return None
        
        start_line = node.start_point[0] + 1
        code = self._get_text(node, content)
        
        return CodeBlock(
            type=block_type,
            signature=signature,
            code=code,
            comment='',
            file=file_path,
            class_name=class_name,
            start_line=start_line
        )
    
    def _get_text(self, node, content: str) -> str:
        """获取节点的文本"""
        return content[node.start_byte:node.end_byte]
