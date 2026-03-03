"""
测试Java解析器是否能正确提取注释
"""

from rag.code_rag.java_parser import JavaMethodParser

# 测试文件
java_file = '/home/juu/unittest/data/project/gson/gson/src/main/java/com/google/gson/stream/JsonReader.java'

# 解析
parser = JavaMethodParser(java_file)
code_blocks = parser.get_code_blocks()

print(f"\n提取到 {len(code_blocks)} 个方法\n")

# 显示所有方法的签名
for i, block in enumerate(code_blocks, 1):
    print(f"  方法 #{i}: {block['method_signature'][:80]}")
    print(f"      注释内容: {block['comment']}"[:500])

print("\n" + "="*80)
print("查找 setNestingLimit 方法...")
print("="*80 + "\n")

# 查找并显示 setNestingLimit 方法
for i, block in enumerate(code_blocks, 1):
    if 'setNestingLimit' in block['method_signature']:
        print("="*80)
        print(f"方法 #{i}")
        print(f"方法签名: {block['method_signature'][:100]}")
        print(f"注释内容:")
        print(f"  {block['comment']}")
        print(f"注释长度: {len(block['comment'])} 字符")
        print(f"代码长度: {len(block['code'])} 字符")
        print("="*80 + "\n")