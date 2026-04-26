#!/usr/bin/env python3
"""
简化测试：验证GLM-4.6模型生成JsonWriter.value(Number)测试代码
"""

import os
import asyncio
from llm.llm import generate_test

async def test_jsonwriter_value():
    print("=" * 60)
    print("测试 GLM-4.6 模型生成 JsonWriter.value(Number) 测试代码")
    print("=" * 60)
    
    # JsonWriter.value(Number) 方法的测试
    result = await generate_test(
        class_name="JsonWriter",
        method_signature="public JsonWriter value(Number value)",
        method_code="""public JsonWriter value(Number value) throws IOException {
    if (value == null) {
        return nullValue();
    }
    
    String string = value.toString();
    if (string.equals("-Infinity") || string.equals("Infinity") || string.equals("NaN")) {
        throw new IllegalArgumentException(
            "Numeric values must be finite, but was " + value);
    }
    
    beforeValue();
    out.append(string);
    return this;
}""",
        output_path="/tmp/test_jsonwriter_value.java",
        test_class_name="JsonWriter_value_Test",
        full_class_name="com.google.gson.stream.JsonWriter",
        package_name="com.google.gson.stream"
    )
    
    print("生成结果:")
    print("成功:", result.get('success', False))
    
    if result.get('success'):
        output_path = result.get('output_path', '/tmp/test_jsonwriter_value.java')
        if os.path.exists(output_path):
            with open(output_path, 'r', encoding='utf-8') as f:
                code = f.read()
            print("代码长度:", len(code))
            print("\n生成的代码预览（前500字符）:")
            print(code[:500] + "..." if len(code) > 500 else code)
        else:
            print("文件未找到:", output_path)
    else:
        print("错误信息:", result.get('error', 'Unknown error'))
    
    print("\n测试完成！")

if __name__ == "__main__":
    asyncio.run(test_jsonwriter_value())
