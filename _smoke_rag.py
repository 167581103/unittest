"""临时 smoke 脚本：跑一次 AgenticRAG.retrieve() 验证生成阶段的 RAG 上下文装配是否健康。
用完可删。
"""
import asyncio
import sys

sys.path.insert(0, "/data/workspace/unittest")
from rag import AgenticRAG


async def main():
    rag = AgenticRAG(
        "/tmp/gson_code_rag.index",
        test_dir="/data/workspace/unittest/data/project/gson/gson/src/test/java",
        verbose=False,
    )

    code = (
        "public <T> T fromJson(String json, Class<T> classOfT) throws JsonSyntaxException {\n"
        "    Object object = fromJson(json, (Type) classOfT);\n"
        "    return Primitives.wrap(classOfT).cast(object);\n"
        "}"
    )

    ctx = await rag.retrieve(
        code=code,
        cls="Gson",
        target_class="com.google.gson.Gson",
        method_signature="public <T> T fromJson(String json, Class<T> classOfT)",
    )
    print("=== RAG retrieved context length:", len(ctx), "chars ===")
    markers = [
        "Available Public Methods",
        "Project Test Framework",
        "OVERLOAD DISAMBIGUATION",
        "VISIBILITY WARNING",
        "### Method:",
        "Existing Test Patterns",
    ]
    for m in markers:
        hit = m in ctx
        tag = "[HIT] " if hit else "[MISS]"
        print("  " + tag + " contains: " + m)
    print()
    print("=== first 800 chars ===")
    print(ctx[:800])


asyncio.run(main())
