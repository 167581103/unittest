# Core Module

核心流水线脚本。

## 文件

- `batch_test_cross_class.py` — **主入口**，跨类批量生成测试并评估覆盖率。
  用法：`python core/batch_test_cross_class.py`
- `fix_loop.py` — 编译错误检测 + LLM/规则修复循环，被 `batch_test_cross_class.py` 调用。

## 相关入口（在仓库根目录）

- `smoke_test.py` — 单方法 smoke：验证 `analyze_method` → `generate_test` → 评估 的完整链路。
- `verify_fixloop_single.py` — 在 `TARGETS[n]` 上跑单条目标，用于调试 FixLoop。
