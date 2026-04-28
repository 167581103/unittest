# archive/legacy —— 早期探索阶段的已归档脚本

以下脚本保留在这里仅为可追溯性。**当前不再使用**，也不会被 [../../main.py](../../main.py) 调起。

| 归档脚本 | 曾经的角色 | 被谁替代 |
|---|---|---|
| `batch_test_cross_class.py` | 早期的批量跨类实验脚本，`TARGETS` 硬编码在代码里 | [../../experiments/run_batch.py](../../experiments/run_batch.py) |
| `verify_fixloop_single.py`  | 从 `batch_test_cross_class.TARGETS[9]` 里挑一条跑 FixLoop 调试 | `python main.py run --limit 1` |
| `smoke_test.py`             | 硬编码 `Streams.parse` 的端到端 smoke 验证 | `python main.py run --limit 1` |

保留理由：实验记录 / 排查历史问题。
