# experiments —— 批量实验模块

## 入口

推荐通过根目录的 `main.py` 调用：

```bash
python main.py pick --top 12          # 选候选方法
python main.py run --llm-concurrency 4  # 跑批量实验
python main.py report                  # 看最新报告
```

也可以直接调：

```bash
python experiments/pick_methods.py --top 15
python experiments/run_batch.py --llm-concurrency 4
```

## 流水线（归一化四阶段）

```
Phase 1 (并行 LLM)        Phase 2 (串行 Eval)                      Phase 3 (汇总)
┌───────────────┐        ┌──────────────────────────────────┐    ┌─────────────┐
│ STAGE 1  GEN  │───→──→ │ STAGE 2  PREFIX   确定性修复     │    │ STAGE 5     │
│               │        │ STAGE 3  EVAL     编译+测试+覆盖率│───→│  REPORT     │
│               │        │ STAGE 4  FIXLOOP  LLM+RAG 修复   │    │ JSON+MD     │
└───────────────┘        └──────────────────────────────────┘    └─────────────┘
```

## CLI 参数（`experiments/run_batch.py`）

| 参数 | 默认 | 说明 |
|---|---|---|
| `--limit N` | 无 | 只跑前 N 个方法（smoke） |
| `--llm-concurrency N` | 4 | Phase1 的 LLM 并发数 |
| `--one-shot` | 关 | 一步式生成（消融对照） |
| `--fix-retries N` | 3 | FixLoop 最大迭代轮数，`0` 表示禁用 |
| `--suffix STR` | 空 | 输出文件名后缀 |
| `--filter-unrunnable` | 关 | 运行前过滤 private 方法 / 仅私有构造器类的实例方法 |

## 输出位置

- 候选方法集：[methods.yaml](./methods.yaml)
- 最终实验结果：`/data/workspace/unittest/experiment_results/`
  - `experiment_summary_twostep_<时间戳>.json`（详细 JSON）
  - `experiment_summary_twostep_<时间戳>.md`（人类可读 Markdown 表）
  - 一步式对比会多一组 `experiment_summary_oneshot_*`
- 生成的测试代码：`/tmp/batch_generated/`

**最终交付数据**：[../experiment_results/experiment_summary_twostep_20260427_220714_top12_new.md](../experiment_results/experiment_summary_twostep_20260427_220714_top12_new.md)

历史中间实验结果已归档到 [../experiment_results/archive/](../experiment_results/archive/)。

## 为什么 Phase1 并行 + Phase2 串行

- **Phase 1（LLM 生成）**：纯网络 IO，线程安全，`asyncio.gather` + `Semaphore(4)` 并发
- **Phase 2（编译+测试+JaCoCo+FixLoop）**：共享 `/tmp/gson-jacoco.exec` 和 Maven `target/` 目录，**必须串行**
- AgenticRAG 索引在第一次进 FixLoop 时才加载一次，全批次复用

## 失败归因标签

脚本会自动对**最终仍编译失败**的用例打标签（FixLoop 拾回的不算失败）：

| 标签 | 含义 |
|------|------|
| `cannot_find_symbol` | 方法/变量找不到（上下文不全或 LLM 幻想 API） |
| `incompatible_types` | 类型不兼容（含泛型） |
| `reference_ambiguous` | 重载歧义 |
| `unreported_exception` | 没声明 throws |
| `generic_inference` | 泛型推断失败 |
| `syntax_error` | 代码被截断 |
| `llm_gen_failed` | LLM 阶段就挂了（没到编译） |
| `private_access` / `deprecation_error` / `werror_warning` | 私有访问 / 已废弃 / Werror |
