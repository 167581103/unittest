#!/usr/bin/env python3
"""
Smoke test: 验证骨架+逐方法生成流程走通真实 LLM 调用，
并用 TestEvaluator 在真实 gson 项目里编译+运行+算覆盖率。
"""
import os
import asyncio
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm.llm import generate_test, analyze_method
from evaluation.evaluator import TestEvaluator, print_report


PROJECT_DIR = "/data/workspace/unittest/data/project/gson"
JACOCO_HOME = "/data/workspace/unittest/lib"
OUT_PATH = "/tmp/smoke_Streams_parse_Test.java"

METHOD_CODE = """/** Takes a reader in any state and returns the next value as a JsonElement. */
public static JsonElement parse(JsonReader reader) throws JsonParseException {
    boolean isEmpty = true;
    try {
        JsonToken unused = reader.peek();
        isEmpty = false;
        return JsonElementTypeAdapter.ADAPTER.read(reader);
    } catch (EOFException e) {
        /*
         * For compatibility with JSON 1.5 and earlier, we return a JsonNull for
         * empty documents instead of throwing.
         */
        if (isEmpty) {
            return JsonNull.INSTANCE;
        }
        // The stream ended prematurely so it is likely a syntax error.
        throw new JsonSyntaxException(e);
    } catch (MalformedJsonException e) {
        throw new JsonSyntaxException(e);
    } catch (IOException e) {
        throw new JsonIOException(e);
    } catch (NumberFormatException e) {
        throw new JsonSyntaxException(e);
    }
}"""

CONTEXT = (
    "Available Public Methods:\n"
    "- com.google.gson.stream.JsonReader(Reader in): constructor\n"
    "- com.google.gson.stream.JsonReader#setLenient(boolean): void\n"
    "- com.google.gson.stream.JsonReader#setStrictness(Strictness): void\n"
    "- com.google.gson.JsonElement#isJsonNull(): boolean\n"
    "- com.google.gson.JsonElement#isJsonArray(): boolean\n"
    "- com.google.gson.JsonElement#isJsonObject(): boolean\n"
    "- com.google.gson.JsonElement#isJsonPrimitive(): boolean\n"
    "- com.google.gson.JsonElement#getAsString(): String\n"
    "- com.google.gson.JsonElement#getAsInt(): int\n"
    "- com.google.gson.JsonNull.INSTANCE: static field\n"
    "Exception types that may be thrown:\n"
    "- com.google.gson.JsonSyntaxException (wraps EOFException after data / MalformedJsonException / NumberFormatException)\n"
    "- com.google.gson.JsonIOException (wraps IOException)\n"
    "Note on constructing inputs:\n"
    "- For well-formed JSON: `new JsonReader(new StringReader(\"{\\\"a\\\":1}\"))`\n"
    "- For empty stream: `new JsonReader(new StringReader(\"\"))` should return JsonNull.INSTANCE (not throw)\n"
    "- For truncated/premature EOF: e.g. `\"{\"` or `\"[1,\"` should throw JsonSyntaxException\n"
    "- For malformed JSON: e.g. `\"{bad}\"` should throw JsonSyntaxException\n"
    "- For IOException: wrap a failing Reader (e.g. anonymous Reader whose read() throws IOException)\n"
    "- To simulate NumberFormatException path, a strict JsonReader parsing e.g. `[1e]` / malformed number usually surfaces as JsonSyntaxException via MalformedJsonException; testing just EOF/malformed/IO paths is enough.\n"
)


async def main():
    t0 = time.time()
    print("=" * 60)
    print("Step 1: analyze_method")
    print("=" * 60)

    analysis = await analyze_method(
        class_name="Streams",
        method_signature="public static JsonElement parse(JsonReader reader)",
        method_code=METHOD_CODE,
        context=CONTEXT,
        full_class_name="com.google.gson.internal.Streams",
    )
    cases = analysis["test_cases"]
    print(f"[analyze] {len(cases)} cases")
    for c in cases:
        print(f"  - {c.get('name')} :: {c.get('category')}")

    print("\n" + "=" * 60)
    print("Step 2: generate_test")
    print("=" * 60)
    result = await generate_test(
        class_name="Streams",
        method_signature="public static JsonElement parse(JsonReader reader)",
        method_code=METHOD_CODE,
        output_path=OUT_PATH,
        context=CONTEXT,
        test_class_name="Streams_parse_Test",
        full_class_name="com.google.gson.internal.Streams",
        package_name="com.google.gson.internal",
        test_cases=cases,
    )
    print(f"[generate] {result}")
    if not result.get("success"):
        return 2

    with open(OUT_PATH, encoding="utf-8") as f:
        code = f.read()
    print(f"\n[file] {OUT_PATH} ({len(code)} chars)")
    print(f"[check] brace_ok={code.count('{') == code.count('}')} "
          f"@Test={code.count('@Test')} 耗时={time.time() - t0:.1f}s")

    print("\n" + "=" * 60)
    print("Step 3: Evaluate (compile + run + coverage)")
    print("=" * 60)
    evaluator = TestEvaluator(project_dir=PROJECT_DIR, jacoco_home=JACOCO_HOME)
    report = evaluator.evaluate(
        test_file=OUT_PATH,
        test_class="com.google.gson.internal.Streams_parse_Test",
        target_class="com.google.gson.internal.Streams",
        target_method="parse",
    )
    print_report(report)
    return 0


if __name__ == "__main__":
    exit(asyncio.run(main()))
