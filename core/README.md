# core —— 核心修复循环

只保留一个文件：

- [fix_loop.py](./fix_loop.py) —— 编译错误检测 + 规则修复 + LLM 修复循环

被 [experiments/run_batch.py](../experiments/run_batch.py) 作为 **STAGE 4: FIXLOOP** 调用。

## 对外接口

- `parse_compile_errors(raw_output)` —— 从 Maven stderr 提取结构化编译错误
- `classify_errors(errors)` —— 按错误种类分类（cannot_find_symbol / incompatible_types / ...）
- `rule_fix(code, classified, rag_instance)` —— 规则修复（import 补全、泛型擦除等）
- `fix_compile_errors(...)` —— **主入口**：串起"parse → classify → rule_fix → LLM fix → 重编译"循环

## 说明

早期版本的批量实验脚本 `core/batch_test_cross_class.py` 已归档到
[../archive/legacy/batch_test_cross_class.py](../archive/legacy/batch_test_cross_class.py)，
其能力已被 `experiments/run_batch.py` 完全替代。
