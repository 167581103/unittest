# 批量实验报告

- 生成时间: `20260428_014859`
- 模式: `two_step`
- 候选数: 12
- 生成成功: **11/12** (91.7%)
- 编译成功: **0/12** (0.0%)
  - 首次直接成功（无任何前置改写）: 0
  - 确定性前置修复拾回: **0/10** (0.0%拾回率)
  - FixLoop 拾回: **0/11** (0.0%拾回率)
- 平均行覆盖率提升: **+0.00%**
- 平均分支覆盖率提升: **+0.00%**

## 逐方法结果

| # | 类 | 方法 | 生成 | 编译 | FixLoop | 行覆盖率（前→后, Δ） | 分支覆盖率（前→后, Δ） | 目标方法覆盖率 | 失败归因 |
|---|----|------|------|------|---------|---------------------|----------------------|--------------|---------|
| 1 | `TypeUtils` | `getTypeArguments` | ✓ | ✗ | ✗ 失败 | - | - | - | cannot_find_symbol |
| 2 | `TypeUtils` | `isAssignable` | ✓ | ✗ | ✗ 失败 | - | - | - | cannot_find_symbol |
| 3 | `TypeUtils` | `isAssignable` | ✓ | ✗ | ✗ 失败 | - | - | - | cannot_find_symbol |
| 4 | `TypeUtils` | `isAssignable` | ✓ | ✗ | ✗ 失败 | - | - | - | cannot_find_symbol |
| 5 | `ClassUtils` | `getShortClassName` | ✓ | ✗ | ✗ 失败 | - | - | - | cannot_find_symbol |
| 6 | `ExceptionUtils` | `throwUnchecked` | ✓ | ✗ | ✗ 失败 | - | - | - | cannot_find_symbol |
| 7 | `ClassUtils` | `getPublicMethod` | ✓ | ✗ | ✗ 失败 | - | - | - | cannot_find_symbol |
| 8 | `Conversion` | `binaryToByte` | ✗ | ✗ | - | - | - | - | llm_gen_failed |
| 9 | `StrSubstitutor` | `replaceIn` | ✓ | ✗ | ✗ 失败 | - | - | - | cannot_find_symbol |
| 10 | `SerializationUtils` | `clone` | ✓ | ✗ | ✗ 失败 | - | - | - | cannot_find_symbol |
| 11 | `CharSequenceTranslator` | `translate` | ✓ | ✗ | ✗ 失败 | - | - | - | reached_end_of_file |
| 12 | `BackgroundInitializer` | `get` | ✓ | ✗ | ✗ 失败 | - | - | - | cannot_find_symbol |

## 失败归因分布

| 标签 | 次数 |
|------|------|
| `cannot_find_symbol` | 10 |
| `llm_gen_failed` | 1 |
| `reached_end_of_file` | 1 |