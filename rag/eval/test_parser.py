"""
第一步：Java测试解析器 - 从测试文件中提取单元测试方法，并使用LLM生成自然语言查询
"""

import re
import json
import os
from typing import List, Dict
from pathlib import Path
from langchain_litellm import ChatLiteLLM

# 配置
LLM_MODEL = os.getenv("CHAT_MODEL", "litellm_proxy/Qwen/Qwen2.5-7B-Instruct")
API_KEY = os.getenv("API_KEY")
API_BASE = os.getenv("BASE_URL")

# 输入输出文件
TEST_FILE = "/home/juu/unittest/data/project/gson/gson/src/test/java/com/google/gson/stream/JsonReaderTest.java"
STEP1_OUTPUT = "/home/juu/unittest/rag/eval/step1_test_cases.json"


class JavaTestParser:
    """解析Java测试文件，提取测试方法并使用LLM生成查询"""

    def __init__(self, test_file_path: str, llm=None):
        self.test_file_path = test_file_path
        self.llm = llm
        self.test_cases = []

    def parse_test_file(self) -> List[Dict]:
        """解析测试文件，提取所有测试方法"""
        with open(self.test_file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 提取所有测试方法
        test_pattern = r'@Test\s*\n\s*public\s+void\s+(\w+)\([^)]*\)\s*(?:throws\s+[^{]*)?\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}'
        matches = re.finditer(test_pattern, content, re.DOTALL)

        for match in matches:
            test_name = match.group(1)
            test_body = match.group(2).strip()

            # 提取使用的JsonReader方法
            methods_used = self._extract_methods_from_test(test_body)

            # 跳过没有使用任何方法的测试
            if not methods_used:
                continue

            test_case = {
                'test_name': test_name,
                'test_code': test_body,
                'methods_used': methods_used
            }
            self.test_cases.append(test_case)

        return self.test_cases

    def _extract_methods_from_test(self, test_body: str) -> List[str]:
        """从测试代码中提取使用的方法"""
        method_pattern = r'reader\.(\w+)\s*\('
        methods = re.findall(method_pattern, test_body)
        return list(set(methods))

    def generate_query_with_llm(self, test_case: Dict) -> str:
        """使用LLM根据测试方法生成自然语言查询"""
        prompt = f"""你是一个专业的代码分析专家。请根据以下Java测试方法，生成一个自然语言的查询问题，该问题应该能够从代码文档中找到对应的答案。

        测试方法名：{test_case['test_name']}
        测试代码：
        {test_case['test_code']}
        使用的JsonReader方法：{test_case['methods_used']}

        分析该测试要测试的具体业务逻辑和相关步骤，生成一个可能的相关用户查询。
        直接输出查询，不要有任何额外的解释或说明。"""

        messages = [
            ("system", "你是一个专业的代码分析专家，擅长从测试代码中提取业务逻辑并生成查询问题。"),
            ("human", prompt)
        ]

        response = self.llm.invoke(messages)
        query = response.content.strip()
        # 移除可能的引号和多余空格
        query = query.strip('"').strip("'").strip()
        return query

    def generate_dataset(self) -> List[Dict]:
        """生成完整的数据集，包含测试信息和LLM生成的查询"""
        print(f"解析测试文件...")
        test_cases = self.parse_test_file()
        print(f"提取到 {len(test_cases)} 个测试用例")

        dataset = []
        for i, test_case in enumerate(test_cases, 1):
            print(f"\n[{i}/{len(test_cases)}] 处理测试: {test_case['test_name']}")

            # 使用LLM生成查询
            query = self.generate_query_with_llm(test_case)
            print(f"  生成的查询: {query}")

            # 构建数据集条目
            dataset_entry = {
                "test_name": test_case['test_name'],
                "test_code": test_case['test_code'],
                "methods_used": test_case['methods_used'],
                "query": query
            }
            dataset.append(dataset_entry)

        return dataset


def main():
    """主函数 - 第一步：生成测试用例数据集"""
    separator = "=" * 60
    print(separator)
    print("第一步：从测试文件提取测试用例并生成查询")
    print(separator)

    # 检查文件是否已存在
    if os.path.exists(STEP1_OUTPUT):
        print(f"\n数据集文件已存在: {STEP1_OUTPUT}")
        print("如需重新生成，请删除该文件后再次运行。")
        with open(STEP1_OUTPUT, 'r', encoding='utf-8') as f:
            dataset = json.load(f)
        print(f"加载已有数据集，共 {len(dataset)} 条记录")
        return dataset

    # 初始化LLM
    print("\n初始化LLM...")
    llm = ChatLiteLLM(model=LLM_MODEL, api_key=API_KEY, api_base=API_BASE)

    # 解析测试文件
    parser = JavaTestParser(TEST_FILE, llm=llm)

    # 生成数据集
    dataset = parser.generate_dataset()

    # 保存数据集
    print(f"\n保存数据集到: {STEP1_OUTPUT}")
    with open(STEP1_OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    print(f"\n完成！共生成 {len(dataset)} 条测试用例记录")

    return dataset


if __name__ == "__main__":
    main()
