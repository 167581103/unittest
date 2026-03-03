"""
Java方法解析器 - 提取方法及其注释
"""

import re
from typing import List, Dict
from tree_sitter import Node, Parser, Language
import tree_sitter_java


class JavaMethodParser:
    """Java方法解析器 - 提取方法及其注释"""

    def __init__(self, java_file_path: str):
        self.java_file_path = java_file_path
        self.code_blocks = []

        # 初始化tree-sitter解析器
        JAVA_LANGAUGE = Language(tree_sitter_java.language())
        self.parser = Parser(JAVA_LANGAUGE)

        self._parse_file()

    def _extract_summary_from_code(self, method_node: Node, source_code: str) -> str:
        """从代码中提取简要说明（用于没有注释的方法）"""
        # 从方法节点中提取方法名
        method_name = None
        for child in method_node.children:
            if child.type == 'identifier':
                method_name = source_code[child.start_byte:child.end_byte]
                break

        if method_name:
            return f"Method: {method_name} (no documentation)"
        return "No comment available"

    def _clean_javadoc(self, comment: str) -> str:
        """清理JavaDoc注释文本，保留有用的信息"""
        # 移除JavaDoc开始和结束标记
        comment = re.sub(r'/\*\*', '', comment)
        comment = re.sub(r'\*/', '', comment)

        # 移除行首的 * 和空格
        lines = comment.split('\n')
        cleaned_lines = []
        for line in lines:
            # 移除行首的 * 和可选的空格
            line = re.sub(r'^\s*\*\s?', '', line)
            cleaned_lines.append(line)

        comment = '\n'.join(cleaned_lines)

        # 处理JavaDoc内联标签
        # {@link Class#method} 或 {@link Class} -> Class#method 或 Class
        comment = re.sub(r'\{@link\s+([^}]+)\}', r'\1', comment)
        # {@linkplain Class#method} -> Class#method
        comment = re.sub(r'\{@linkplain\s+([^}]+)\}', r'\1', comment)
        # {@code something} -> something
        comment = re.sub(r'\{@code\s+([^}]+)\}', r'\1', comment)
        # {@value field} -> field (或其值，如果可以获取)
        comment = re.sub(r'\{@value\s+([^}]+)\}', r'\1', comment)
        # {@inheritDoc} -> [inherited]
        comment = re.sub(r'\{@inheritDoc\}', '[inherited]', comment)

        # 处理HTML标签，特别是保留 code 和 pre 标签的内容
        # 先处理 <code> 和 </code>，保留内容
        comment = re.sub(r'<code>([^<]+)</code>', r'\1', comment)
        comment = re.sub(r'<pre>([^<]+)</pre>', r'\1', comment)
        # 移除其他HTML标签但保留内容
        comment = re.sub(r'<([a-z][a-z0-9]*)\b[^>]*>([^<]*)</\1>', r'\2', comment)
        comment = re.sub(r'<([a-z][a-z0-9]*)\b[^>]*/>', '', comment)
        comment = re.sub(r'</[a-z][a-z0-9]*>', '', comment)
        comment = re.sub(r'<[a-z][a-z0-9]*\b[^>]*>', '', comment)

        # 处理JavaDoc标签，提取有用信息
        # @param name description -> 参数 name: description
        def process_param(match):
            return f"参数 {match.group(1)}: {match.group(2).strip()}" if match.group(2) else f"参数 {match.group(1)}"
        comment = re.sub(r'@param\s+(\w+)\s*(.*?)(?=\n\s*@|\n\s*$|$)', process_param, comment, flags=re.MULTILINE)

        # @return description -> 返回: description
        def process_return(match):
            return f"返回: {match.group(1).strip()}" if match.group(1).strip() else "返回:"
        comment = re.sub(r'@return\s+(.*?)(?=\n\s*@|\n\s*$|$)', process_return, comment, flags=re.MULTILINE)

        # @throws ExceptionClass description -> 抛出: ExceptionClass - description
        def process_throws(match):
            return f"抛出: {match.group(1)} - {match.group(2).strip()}" if match.group(2).strip() else f"抛出: {match.group(1)}"
        comment = re.sub(r'@throws\s+(\S+)\s+(.*?)(?=\n\s*@|\n\s*$|$)', process_throws, comment, flags=re.MULTILINE)

        # @exception ExceptionClass description -> 异常: ExceptionClass - description
        def process_exception(match):
            return f"异常: {match.group(1)} - {match.group(2).strip()}" if match.group(2).strip() else f"异常: {match.group(1)}"
        comment = re.sub(r'@exception\s+(\S+)\s+(.*?)(?=\n\s*@|\n\s*$|$)', process_exception, comment, flags=re.MULTILINE)

        # @since version -> since version
        comment = re.sub(r'@since\s+(.*?)(?=\n\s*@|\n\s*$|$)', r'since \1', comment, flags=re.MULTILINE)

        # @deprecated -> [已弃用]
        comment = re.sub(r'@deprecated\s*(.*?)(?=\n\s*@|\n\s*$|$)', r'[已弃用] \1', comment, flags=re.MULTILINE)

        # @see reference -> 参见: reference
        comment = re.sub(r'@see\s+(.*?)(?=\n\s*@|\n\s*$|$)', r'参见: \1', comment, flags=re.MULTILINE)

        # 移除多余的空白行
        comment = re.sub(r'\n\s*\n+', '\n\n', comment)

        # 压缩行内多余空格（但保留换行）
        lines = comment.split('\n')
        cleaned_lines = []
        for line in lines:
            # 压缩行内多余空格
            line = re.sub(r' +', ' ', line)
            # 移除行首尾空格
            line = line.strip()
            if line:  # 保留非空行
                cleaned_lines.append(line)

        comment = '\n'.join(cleaned_lines)

        return comment.strip()

    def _extract_preceding_comment(self, node: Node, source_lines: List[str]) -> str:
        """提取节点之前的JavaDoc注释"""
        # 找到节点所在的行号
        start_line = node.start_point[0]

        # 向前查找最近的JavaDoc注释
        line_idx = start_line - 1
        javadoc_start_idx = -1
        while line_idx >= 0:
            line = source_lines[line_idx]
            stripped = line.strip()

            if stripped.startswith('/**'):
                # 找到JavaDoc开始位置
                javadoc_start_idx = line_idx
                break

            elif stripped and not stripped.startswith('*') and not stripped.startswith('//'):
                # 遇到非注释行，停止查找
                break

            line_idx -= 1

        # 如果找到了JavaDoc注释
        if javadoc_start_idx >= 0:
            comment_lines = []
            current_idx = javadoc_start_idx

            # 向下收集完整的JavaDoc注释（直到遇到 */）
            while current_idx < len(source_lines):
                line = source_lines[current_idx]
                comment_lines.append(line)
                if '*/' in line:
                    break
                current_idx += 1

            comment_text = '\n'.join(comment_lines)
            return self._clean_javadoc(comment_text)

        return ""

    def _extract_method_code(self, method_node: Node, source_code: bytes) -> str:
        """提取方法完整代码"""
        return source_code[method_node.start_byte:method_node.end_byte].decode('utf-8')

    def _parse_file(self):
        """解析Java文件，提取方法及其注释"""
        with open(self.java_file_path, 'rb') as f:
            source_code = f.read()

        # 读取源代码行用于注释提取
        with open(self.java_file_path, 'r', encoding='utf-8') as f:
            source_lines = f.readlines()

        # 使用tree-sitter解析
        tree = self.parser.parse(source_code)
        root_node = tree.root_node

        # 遍历AST树提取所有方法
        self._traverse_and_extract_methods(root_node, source_code, source_lines)

    def _traverse_and_extract_methods(self, node: Node, source_code: bytes, source_lines: List[str]):
        """遍历AST树提取所有方法"""
        if node.type == 'method_declaration':
            # 提取方法信息
            method_code = self._extract_method_code(node, source_code)

            # 尝试提取注释
            comment_text = self._extract_preceding_comment(node, source_lines)

            # 如果没有注释，从代码提取简要说明
            if not comment_text:
                comment_text = self._extract_summary_from_code(node, source_code.decode('utf-8'))

            # 提取方法签名
            signature_node = node.child_by_field_name('declarator')
            if signature_node:
                signature = source_code[signature_node.start_byte:signature_node.end_byte].decode('utf-8')
            else:
                # 如果没有declarator字段，尝试从children中提取
                signature = method_code.split('{')[0].strip()

            code_block = {
                'method_signature': signature,
                'comment': comment_text,
                'code': method_code,
                'start_line': node.start_point[0] + 1,
                'end_line': node.end_point[0] + 1,
                'file': self.java_file_path
            }

            self.code_blocks.append(code_block)
            print(f"  提取方法: {signature[:80]}...")

        # 递归遍历子节点
        for child in node.children:
            self._traverse_and_extract_methods(child, source_code, source_lines)

    def get_code_blocks(self) -> List[Dict]:
        """获取所有提取的代码块"""
        return self.code_blocks
