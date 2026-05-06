#!/usr/bin/env python3
"""验证 pick_methods 修复后的方法识别准确性。

覆盖：
  1) mask 工具：{@link ...} 被替换为等长空白
  2) javadoc 抗干扰：getTypeArguments 不再被 javadoc 污染
  3) 行号 hint：多个同名重载能被 JaCoCo XML 的 line 精准区分
  4) 在真实 commons-lang JaCoCo XML 上做端到端 pick 抽取，无 @link 污染
"""
import sys
sys.path.insert(0, '/data/workspace/unittest')
import xml.etree.ElementTree as ET
from experiments.pick_methods import (
    _mask_comments_and_strings,
    _extract_method_snippet,
)

# ── 1) mask 工具
sample = """
public class TypeUtils {
    /**
     * Wraps {@link TypeUtils#getTypeArguments(Type, Class)}.
     */
    public static Type unrollVariables(Map<TypeVariable<?>, Type> typeArguments, final Type type) {
        if (typeArguments == null) { typeArguments = Collections.emptyMap(); }
        return unrollVariables(typeArguments, type, new HashSet<>());
    }

    public static Map<TypeVariable<?>, Type> getTypeArguments(final Type type, final Class<?> toClass) {
        return getTypeArguments(type, toClass, null);
    }
}
"""
print("=== 1) mask sanity ===")
masked = _mask_comments_and_strings(sample)
assert len(masked) == len(sample)
idx = sample.find('{@link')
print("   original@javadoc 10:", repr(sample[idx:idx+10]))
print("   masked   @javadoc 10:", repr(masked[idx:idx+10]))
assert masked[idx:idx+10].strip() == ''
print("   OK")

# ── 2) javadoc 抗干扰
print()
print("=== 2) javadoc anti-pollution ===")
s = _extract_method_snippet(sample, 'getTypeArguments')
assert s is not None
print("   sig =", repr(s['signature'][:80]))
assert s['signature'].lstrip().startswith('public static Map'), s['signature']
print("   OK")

# ── 3) 真实 commons-lang TypeUtils + JaCoCo line hint
print()
print("=== 3) real commons-lang (with line hint) ===")
java_path = '/data/workspace/unittest/data/project/commons-lang/src/main/java/org/apache/commons/lang3/reflect/TypeUtils.java'
with open(java_path, encoding='utf-8') as f:
    real = f.read()

xml_path = '/tmp/pick_methods_report_commons-lang.xml'
tree = ET.parse(xml_path)
type_utils_cls = None
for pkg in tree.getroot().findall('.//package'):
    if pkg.get('name') == 'org/apache/commons/lang3/reflect':
        for cls in pkg.findall('class'):
            if cls.get('name') == 'org/apache/commons/lang3/reflect/TypeUtils':
                type_utils_cls = cls
                break
        break

print("   method :: hint -> picked signature")
for m in type_utils_cls.findall('method'):
    mname = m.get('name')
    if mname in ('<init>', '<clinit>'):
        continue
    desc = m.get('desc', '')
    line = m.get('line')
    try:
        line_int = int(line) if line else None
    except Exception:
        line_int = None
    sr = _extract_method_snippet(real, mname, desc=desc, start_line_hint=line_int)
    sig = sr['signature'][:90] if sr else 'NO MATCH'
    print(f"   {mname:25s} :: line={line_int} -> {sig}")
    # 断言：对 isAssignable 的 7 个重载，必须各有 start_line 不同
    if sr:
        # 重要断言：signature 不能包含 @link/@code/@see
        assert '@link' not in sr['signature']
        assert '@code' not in sr['signature']

# ── 4) 重载区分：7 个 isAssignable 应各自落到不同起始行
print()
print("=== 4) overload differentiation (isAssignable) ===")
picks = []
for m in type_utils_cls.findall('method'):
    if m.get('name') != 'isAssignable':
        continue
    line = m.get('line')
    try:
        line_int = int(line)
    except Exception:
        line_int = None
    sr = _extract_method_snippet(real, 'isAssignable',
                                 desc=m.get('desc', ''),
                                 start_line_hint=line_int)
    if sr:
        picks.append((line_int, sr['start_line'], sr['signature'][:80]))
for p in picks:
    print(f"   hint={p[0]}  pick_start={p[1]}  sig={p[2]}")
# 期望：每个 hint 对应的 pick_start_line 都不同
pick_starts = [p[1] for p in picks]
assert len(set(pick_starts)) == len(pick_starts), f"重载未被正确区分: {pick_starts}"
print("   OK: 所有重载各自对应不同的 start_line")

print()
print("✅ ALL CHECKS PASSED")
