"""
模块化测试脚本 - 独立测试每个模块

使用示例：
    # 测试单个模块
    python core/test_modules.py --module agentic    # 测试 Agentic RAG 实体提取
    python core/test_modules.py --module rag        # 测试 Code RAG 检索
    python core/test_modules.py --module llm        # 测试 LLM 测试生成
    python core/test_modules.py --module eval       # 测试评估器
    
    # 测试多个模块（串联）
    python core/test_modules.py --modules agentic,rag,llm
    
    # 使用自定义输入
    python core/test_modules.py --module llm --context "..."
"""

import os
import sys
import json
import asyncio
import argparse
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============ 配置 ============

PROJECT_DIR = "/home/juu/unittest/data/project/gson/gson/src/main/java"
INDEX_PATH = "/tmp/gson_code_rag.index"
OUTPUT_DIR = "/tmp/module_test_output"

# 示例方法
SAMPLE_METHOD = '''public void skipValue() throws IOException {
    int count = 0;
    do {
      int p = peeked;
      if (p == PEEKED_NONE) {
        p = doPeek();
      }
      switch (p) {
        case PEEKED_BEGIN_ARRAY:
          push(JsonScope.EMPTY_ARRAY);
          count++;
          break;
        case PEEKED_BEGIN_OBJECT:
          push(JsonScope.EMPTY_OBJECT);
          count++;
          break;
        case PEEKED_END_ARRAY:
          stackSize--;
          count--;
          break;
        case PEEKED_END_OBJECT:
          if (count == 0) {
            pathNames[stackSize - 1] = null;
          }
          stackSize--;
          count--;
          break;
        default:
          peeked = PEEKED_NONE;
      }
    } while (count > 0);
    pathIndices[stackSize - 1]++;
  }'''

SAMPLE_CLASS = "JsonReader"
SAMPLE_SIGNATURE = "public void skipValue() throws IOException"


# ============ 数据结构 ============

@dataclass
class AgenticResult:
    """Agentic RAG 提取结果"""
    methods: List[str]
    fields: List[str]
    types: List[str]
    raw_response: str
    success: bool
    error: str = None


@dataclass
class RAGResult:
    """Code RAG 检索结果"""
    context: str
    found_items: List[str]
    not_found_items: List[str]
    context_length: int


@dataclass
class LLMResult:
    """LLM 生成结果"""
    test_code: str
    output_path: str
    success: bool
    error: str = None


@dataclass
class EvalResult:
    """评估结果"""
    compilation_success: bool
    test_count: int
    passed_count: int
    errors: List[str]
    coverage_line: float
    coverage_branch: float


# ============ 模块1: Agentic RAG 实体提取 ============

async def test_agentic_extraction(
    code: str = SAMPLE_METHOD,
    cls: str = SAMPLE_CLASS
) -> AgenticResult:
    """
    模块1: 测试 Agentic RAG 实体提取
    
    输入: 代码片段、类名
    输出: AI 提取的依赖（方法、字段、类型）
    """
    print("\n" + "=" * 60)
    print("模块1: Agentic RAG 实体提取")
    print("=" * 60)
    
    print(f"\n[输入]")
    print(f"  类名: {cls}")
    print(f"  代码长度: {len(code)} 字符")
    print(f"  代码预览: {code[:100]}...")
    
    from rag import AgenticRAG
    
    # 如果索引不存在，先构建
    if not os.path.exists(INDEX_PATH):
        print("\n[!] 索引不存在，正在构建...")
        from rag import CodeRAG
        rag = CodeRAG()
        rag.build_index(PROJECT_DIR, INDEX_PATH, batch_size=50)
    
    agentic = AgenticRAG(INDEX_PATH, verbose=True)
    
    print("\n[处理] 调用 LLM 分析依赖...")
    try:
        deps = await agentic.analyze_dependencies(code, cls)
        
        result = AgenticResult(
            methods=deps.get("methods", []),
            fields=deps.get("fields", []),
            types=deps.get("types", []),
            raw_response=str(deps),
            success=True
        )
        
        print(f"\n[输出]")
        print(f"  方法依赖: {result.methods}")
        print(f"  字段依赖: {result.fields}")
        print(f"  类型依赖: {result.types}")
        print(f"  ✓ 提取成功")
        
        return result
        
    except Exception as e:
        print(f"  ✗ 提取失败: {e}")
        return AgenticResult([], [], [], "", False, str(e))


# ============ 模块2: Code RAG 检索 ============

def test_rag_retrieval(
    entities: Dict[str, List[str]] = None,
    code: str = SAMPLE_METHOD,
    cls: str = SAMPLE_CLASS,
    method_signature: str = SAMPLE_SIGNATURE
) -> RAGResult:
    """
    模块2: 测试 Code RAG 检索
    
    输入: 实体列表、代码
    输出: 检索到的上下文
    """
    print("\n" + "=" * 60)
    print("模块2: Code RAG 检索")
    print("=" * 60)
    
    # 默认实体
    if entities is None:
        entities = {
            "methods": ["doPeek", "push", "skipQuotedValue"],
            "fields": ["peeked", "stackSize"],
            "types": ["JsonScope"]
        }
    
    print(f"\n[输入]")
    print(f"  方法实体: {entities.get('methods', [])}")
    print(f"  字段实体: {entities.get('fields', [])}")
    print(f"  类型实体: {entities.get('types', [])}")
    
    from rag import CodeRAG
    
    if not os.path.exists(INDEX_PATH):
        print("\n[!] 索引不存在，请先运行 agentic 模块或手动构建索引")
        return RAGResult("", [], [], 0)
    
    rag = CodeRAG(INDEX_PATH)
    
    print("\n[处理] 检索相关上下文...")
    found = []
    not_found = []
    context_parts = []
    
    # 检索类信息
    if cls in rag.class_info:
        info = rag.class_info[cls]
        found.append(f"类定义: {cls}")
        context_parts.append(f"## {info.package}.{cls}")
        
        if info.imports:
            context_parts.append("\n### Imports\n" + "\n".join(info.imports[:10]))
    
    # 检索方法
    for method_name in entities.get("methods", []):
        for block in rag.blocks:
            if method_name in block.signature and block.class_name == cls:
                found.append(f"方法: {method_name}")
                context_parts.append(f"\n### {block.signature}\n```java\n{block.code[:500]}\n```")
                break
        else:
            not_found.append(f"方法: {method_name}")
    
    # 检索类型
    for type_name in entities.get("types", []):
        if type_name in rag.class_info:
            info = rag.class_info[type_name]
            found.append(f"类型: {type_name}")
            context_parts.append(f"\n### {info.package}.{type_name}")
            if info.constants:
                context_parts.append("Constants: " + ", ".join(c.get("name", "") for c in info.constants[:10]))
        else:
            not_found.append(f"类型: {type_name}")
    
    # 语义搜索
    print("\n[处理] 语义搜索补充...")
    from llm import embed
    query_vector = embed([code])[0]
    results = rag.store.search(query_vector, 3)
    for meta, _ in results:
        idx = meta.get("idx", 0)
        if idx < len(rag.blocks):
            block = rag.blocks[idx]
            context_parts.append(f"\n### Related: {block.signature}\n```java\n{block.code[:400]}\n```")
    
    context = "\n".join(context_parts)
    
    print(f"\n[输出]")
    print(f"  找到: {len(found)} 项")
    for item in found:
        print(f"    ✓ {item}")
    if not_found:
        print(f"  未找到: {len(not_found)} 项")
        for item in not_found:
            print(f"    ✗ {item}")
    print(f"  上下文长度: {len(context)} 字符")
    
    return RAGResult(context, found, not_found, len(context))


# ============ 模块3: LLM 测试生成 ============

async def test_llm_generation(
    code: str = SAMPLE_METHOD,
    context: str = None,
    class_name: str = SAMPLE_CLASS,
    method_signature: str = SAMPLE_SIGNATURE,
    output_path: str = None
) -> LLMResult:
    """
    模块3: 测试 LLM 测试生成
    
    输入: 代码、上下文
    输出: 生成的测试代码
    """
    print("\n" + "=" * 60)
    print("模块3: LLM 测试生成")
    print("=" * 60)
    
    if context is None:
        context = "无上下文"
    
    if output_path is None:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        output_path = os.path.join(OUTPUT_DIR, f"{class_name}_Test.java")
    
    print(f"\n[输入]")
    print(f"  类名: {class_name}")
    print(f"  方法签名: {method_signature}")
    print(f"  代码长度: {len(code)} 字符")
    print(f"  上下文长度: {len(context)} 字符")
    print(f"  输出路径: {output_path}")
    
    from llm import generate_test
    
    print("\n[处理] 调用 LLM 生成测试...")
    try:
        result = await generate_test(
            class_name=class_name,
            method_signature=method_signature,
            method_code=code,
            output_path=output_path,
            context=context,
            test_class_name=f"{class_name}_Test",
            full_class_name=f"com.google.gson.stream.{class_name}",
        )
        
        if result["success"]:
            with open(output_path, 'r', encoding='utf-8') as f:
                test_code = f.read()
            
            print(f"\n[输出]")
            print(f"  ✓ 生成成功")
            print(f"  测试代码长度: {len(test_code)} 字符")
            print(f"  输出文件: {output_path}")
            print(f"\n[代码预览]")
            print("-" * 40)
            print(test_code[:500] + "..." if len(test_code) > 500 else test_code)
            print("-" * 40)
            
            return LLMResult(test_code, output_path, True)
        else:
            print(f"\n[输出] ✗ 生成失败: {result.get('error', 'Unknown error')}")
            return LLMResult("", output_path, False, result.get("error"))
            
    except Exception as e:
        print(f"\n[输出] ✗ 生成失败: {e}")
        return LLMResult("", output_path or "", False, str(e))


# ============ 模块4: Evaluator 评估 ============

def test_evaluator(
    test_file: str = None,
    target_class: str = "com.google.gson.stream.JsonReader",
    target_method: str = "skipValue"
) -> EvalResult:
    """
    模块4: 测试评估器
    
    输入: 测试文件路径
    输出: 编译结果、测试结果、覆盖率
    """
    print("\n" + "=" * 60)
    print("模块4: Evaluator 评估")
    print("=" * 60)
    
    if test_file is None:
        # 查找最近生成的测试文件
        test_files = list(Path(OUTPUT_DIR).glob("*.java"))
        if not test_files:
            print("\n[!] 未找到测试文件，请先运行 llm 模块")
            return EvalResult(False, 0, 0, ["未找到测试文件"], 0, 0)
        test_file = str(sorted(test_files)[-1])
    
    print(f"\n[输入]")
    print(f"  测试文件: {test_file}")
    print(f"  目标类: {target_class}")
    print(f"  目标方法: {target_method}")
    
    from evaluation.evaluator import TestEvaluator
    
    JACOCO_HOME = "/home/juu/unittest/lib/jacoco-0.8.14"
    MAVEN_PROJECT_DIR = "/home/juu/unittest/data/project/gson"
    
    evaluator = TestEvaluator(
        project_dir=MAVEN_PROJECT_DIR,
        jacoco_home=JACOCO_HOME
    )
    
    print("\n[处理] 编译和运行测试...")
    report = evaluator.evaluate(
        test_file=test_file,
        test_class="com.google.gson.stream.JsonReaderTestGenerated",
        target_class=target_class,
        target_method=target_method
    )
    
    errors = []
    for r in report.test_results:
        if not r.passed:
            errors.append(f"{r.name}: {r.error[:100] if r.error else 'Failed'}")
    
    print(f"\n[输出]")
    print(f"  编译状态: {'成功' if report.compilation_success else '失败'}")
    print(f"  测试总数: {len(report.test_results)}")
    print(f"  通过数量: {sum(1 for r in report.test_results if r.passed)}")
    
    if report.coverage:
        print(f"  行覆盖率: {report.coverage.line_coverage:.1f}%")
        print(f"  分支覆盖率: {report.coverage.branch_coverage:.1f}%")
    
    if errors:
        print(f"\n  错误列表:")
        for err in errors[:5]:
            print(f"    - {err}")
    
    return EvalResult(
        compilation_success=report.compilation_success,
        test_count=len(report.test_results),
        passed_count=sum(1 for r in report.test_results if r.passed),
        errors=errors,
        coverage_line=report.coverage.line_coverage if report.coverage else 0,
        coverage_branch=report.coverage.branch_coverage if report.coverage else 0
    )


# ============ 串联测试 ============

async def test_pipeline(modules: List[str] = None):
    """
    串联测试多个模块
    """
    if modules is None:
        modules = ["agentic", "rag", "llm", "eval"]
    
    print("\n" + "=" * 60)
    print(f"串联测试: {' -> '.join(modules)}")
    print("=" * 60)
    
    results = {}
    context = None
    entities = None
    test_file = None
    
    for module in modules:
        if module == "agentic":
            result = await test_agentic_extraction()
            entities = {
                "methods": result.methods,
                "fields": result.fields,
                "types": result.types
            }
            results["agentic"] = asdict(result)
            
        elif module == "rag":
            result = test_rag_retrieval(entities)
            context = result.context
            results["rag"] = asdict(result)
            
        elif module == "llm":
            result = await test_llm_generation(context=context)
            test_file = result.output_path if result.success else None
            results["llm"] = asdict(result)
            
        elif module == "eval":
            if test_file:
                result = test_evaluator(test_file=test_file)
            else:
                result = test_evaluator()
            results["eval"] = asdict(result)
    
    # 保存结果
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    result_path = os.path.join(OUTPUT_DIR, "test_results.json")
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n结果已保存到: {result_path}")
    return results


# ============ 主程序 ============

def main():
    parser = argparse.ArgumentParser(description="模块化测试脚本")
    parser.add_argument("--module", "-m", type=str, help="测试单个模块 (agentic/rag/llm/eval)")
    parser.add_argument("--modules", "-M", type=str, help="测试多个模块，逗号分隔 (如: agentic,rag,llm)")
    parser.add_argument("--code", "-c", type=str, help="自定义代码输入")
    parser.add_argument("--class", dest="cls", type=str, help="自定义类名")
    parser.add_argument("--context", type=str, help="自定义上下文 (用于 llm 模块)")
    parser.add_argument("--test-file", type=str, help="测试文件路径 (用于 eval 模块)")
    
    args = parser.parse_args()
    
    # 自定义输入
    code = args.code or SAMPLE_METHOD
    cls = args.cls or SAMPLE_CLASS
    
    if args.module:
        # 单模块测试
        module = args.module.lower()
        
        if module == "agentic":
            asyncio.run(test_agentic_extraction(code, cls))
        elif module == "rag":
            test_rag_retrieval(code=code, cls=cls)
        elif module == "llm":
            context = args.context or "无上下文"
            asyncio.run(test_llm_generation(code=code, context=context, class_name=cls))
        elif module == "eval":
            test_evaluator(test_file=args.test_file)
        else:
            print(f"未知模块: {module}")
            print("可用模块: agentic, rag, llm, eval")
            
    elif args.modules:
        # 多模块串联测试
        modules = [m.strip().lower() for m in args.modules.split(",")]
        asyncio.run(test_pipeline(modules))
        
    else:
        # 默认：运行所有模块
        asyncio.run(test_pipeline())


if __name__ == "__main__":
    main()
