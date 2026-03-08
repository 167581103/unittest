"""
极简通路：RAG检索 + LLM生成单元测试

使用示例：
    conda activate gp
    python core/generate_test_pipeline.py
"""

import os
import sys
import asyncio

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag import CodeRAG, AgenticRAG
from llm import generate_test


# ============ 配置 ============

PROJECT_DIR = "/home/juu/unittest/data/project/gson/gson/src/main/java"
INDEX_PATH = "/tmp/gson_code_rag.index"

TARGET_FILE = "/home/juu/unittest/data/project/gson/gson/src/main/java/com/google/gson/stream/JsonReader.java"
OUTPUT_DIR = "/tmp/generated_tests"


# ============ 目标方法 ============

TARGET_METHOD = '''public void skipValue() throws IOException {
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
        case PEEKED_UNQUOTED:
          skipUnquotedValue();
          break;
        case PEEKED_SINGLE_QUOTED:
          skipQuotedValue('\'');
          break;
        case PEEKED_DOUBLE_QUOTED:
          skipQuotedValue('"');
          break;
        case PEEKED_UNQUOTED_NAME:
          skipUnquotedValue();
          if (count == 0) {
            pathNames[stackSize - 1] = "<skipped>";
          }
          break;
        case PEEKED_SINGLE_QUOTED_NAME:
          skipQuotedValue('\'');
          if (count == 0) {
            pathNames[stackSize - 1] = "<skipped>";
          }
          break;
        case PEEKED_DOUBLE_QUOTED_NAME:
          skipQuotedValue('"');
          if (count == 0) {
            pathNames[stackSize - 1] = "<skipped>";
          }
          break;
        case PEEKED_NUMBER:
          pos += peekedNumberLength;
          break;
        case PEEKED_EOF:
          return;
        default:
          // Do nothing
      }
      peeked = PEEKED_NONE;
    } while (count > 0);

    pathIndices[stackSize - 1]++;
  }'''


# ============ 核心流程 ============

async def build_index():
    """步骤1：构建代码库索引"""
    print("=" * 60)
    print("步骤1：构建代码库索引")
    print("=" * 60)
    
    rag = CodeRAG()
    rag.build_index(PROJECT_DIR, INDEX_PATH, batch_size=50)
    
    print(f"\n✓ 索引构建完成：{INDEX_PATH}\n")
    return rag


async def retrieve_context():
    """步骤2：使用Agentic RAG检索相关上下文"""
    print("=" * 60)
    print("步骤2：Agentic RAG智能检索")
    print("=" * 60)

    agentic_rag = AgenticRAG(INDEX_PATH)
    # Agent分析依赖并检索
    context = await agentic_rag.retrieve_by_agent(TARGET_METHOD, target_class="JsonReader", top_k=3)

    print(f"✓ 检索完成，上下文长度：{len(context)} 字符\n")
    return context


async def generate_unit_test(context: str):
    """步骤3：生成单元测试"""
    print("=" * 60)
    print("步骤3：生成单元测试")
    print("=" * 60)
    
    output_path = os.path.join(OUTPUT_DIR, "JsonReader_skipValue_Test.java")
    
    result = await generate_test(
        class_name="JsonReader",
        method_signature="public void skipValue() throws IOException",
        method_code=TARGET_METHOD,
        output_path=output_path,
        context=context,
        temperature=0.7,
        max_tokens=3000
    )
    
    if result["success"]:
        print(f"✓ 测试生成成功：{result['output_path']}")
        
        # 显示生成的代码
        with open(output_path, 'r', encoding='utf-8') as f:
            code = f.read()
        print(f"\n生成的代码（前1000字符）：\n{code[:1000]}...")
    else:
        print(f"✗ 测试生成失败：{result['error']}")
    
    return result


async def full_pipeline():
    """完整流程"""
    print("\n" + "=" * 60)
    print("RAG + LLM 单元测试生成通路")
    print("=" * 60 + "\n")
    
    # 1. 构建索引（如果不存在）
    if not os.path.exists(INDEX_PATH):
        await build_index()
    else:
        print(f"✓ 索引已存在：{INDEX_PATH}\n")
    
    # 2. 检索上下文
    context = await retrieve_context()
    
    # 显示检索到的上下文
    print("-" * 60)
    print("检索到的上下文：")
    print("-" * 60)
    print(context[:2000] if len(context) > 2000 else context)
    print("-" * 60 + "\n")
    
    # 3. 生成测试
    result = await generate_unit_test(context)
    
    print("\n" + "=" * 60)
    print("流程完成")
    print("=" * 60)
    
    return result


# ============ 便捷函数 ============

def quick_generate(method_code: str, class_name: str, method_signature: str, output_path: str):
    """
    快速生成单元测试（一站式，使用Agentic RAG）

    Args:
        method_code: 目标方法代码
        class_name: 类名
        method_signature: 方法签名
        output_path: 输出路径
    """
    async def run():
        # 1. 确保索引存在
        if not os.path.exists(INDEX_PATH):
            print("[→] 构建索引...")
            rag = CodeRAG()
            rag.build_index(PROJECT_DIR, INDEX_PATH)

        # 2. Agentic RAG检索上下文
        print("[→] Agentic RAG检索...")
        agentic_rag = AgenticRAG(INDEX_PATH)
        context = await agentic_rag.retrieve_by_agent(method_code, target_class=class_name, top_k=3)

        # 3. 生成测试
        print("[→] 生成测试...")
        result = await generate_test(
            class_name=class_name,
            method_signature=method_signature,
            method_code=method_code,
            output_path=output_path,
            context=context
        )

        if result["success"]:
            print(f"✓ 生成成功：{output_path}")
        else:
            print(f"✗ 生成失败：{result['error']}")

        return result

    return asyncio.run(run())


# ============ 主程序 ============

if __name__ == "__main__":
    # 运行完整流程
    asyncio.run(full_pipeline())
    
    # 或者使用便捷函数
    # quick_generate(
    #     method_code=TARGET_METHOD,
    #     class_name="JsonReader",
    #     method_signature="public void skipValue() throws IOException",
    #     output_path="/tmp/JsonReader_skipValue_Test.java"
    # )
