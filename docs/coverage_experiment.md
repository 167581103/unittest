# JaCoCo 覆盖率统计实验

## 1. JaCoCo 工作原理

### 核心概念
- **JaCoCo Agent**: Java 代理，在 JVM 启动时注入，收集代码执行覆盖率
- **exec 文件**: 存储覆盖率数据的二进制文件
- **classfiles**: 编译后的 .class 文件，用于生成覆盖率报告

### run.sh 流程分析
```bash
# 1. 设置 JaCoCo Agent（关键！）
export JAVA_TOOL_OPTIONS="-javaagent:$JACOCO_HOME/lib/jacocoagent.jar=destfile=/tmp/gson-jacoco.exec,append=true"
#   - destfile: 指定 exec 文件路径
#   - append=true: 追加模式，多次运行累积覆盖率

# 2. 运行测试
mvn clean test -pl gson -am -Dtest=com.google.gson.stream.JsonReaderTest
#   - JaCoCo Agent 会自动收集覆盖率数据

# 3. 查看执行信息
java -jar $JACOCO_HOME/lib/jacococli.jar execinfo /tmp/gson-jacoco.exec

# 4. 生成报告
java -jar $JACOCO_HOME/lib/jacococli.jar report /tmp/gson-jacoco.exec \
  --classfiles gson/target/classes \
  --sourcefiles gson/src/main/java \
  --html jacoco-report/html
```

## 2. 关键参数

| 参数 | 说明 |
|------|------|
| `destfile` | exec 文件路径 |
| `append=true` | 追加模式，多次运行累积数据 |
| `append=false` | 覆盖模式，每次运行覆盖旧数据 |

## 3. 实验结果

### 实验1：验证 exec 文件生成 ✅
```bash
# 运行测试
JAVA_TOOL_OPTIONS="-javaagent:.../jacocoagent.jar=destfile=/tmp/test-jacoco.exec" \
mvn test -pl gson -am -Dtest=JsonReaderTest

# exec 文件生成成功
ls -la /tmp/test-jacoco.exec
# -rw-r--r-- 1 juu juu 208605 Mar 15 17:42 /tmp/test-jacoco.exec
```

### 实验2：验证覆盖率报告生成 ✅
```bash
# 生成 CSV 报告
java -jar jacococli.jar report /tmp/test-jacoco.exec \
  --classfiles gson/target/classes \
  --csv /tmp/coverage.csv

# JsonReader 覆盖率数据
# LINE_MISSED: 48, LINE_COVERED: 672
# 行覆盖率: 672/(48+672) = 93.3%
# 分支覆盖率: 441/(74+441) = 85.6%
```

### CSV 格式说明
```
GROUP,PACKAGE,CLASS,INSTRUCTION_MISSED,INSTRUCTION_COVERED,BRANCH_MISSED,BRANCH_COVERED,LINE_MISSED,LINE_COVERED,...
```
- 索引 7: LINE_MISSED
- 索引 8: LINE_COVERED
- 索引 5: BRANCH_MISSED
- 索引 6: BRANCH_COVERED

## 4. 当前代码问题分析

### 问题1：get_baseline_coverage 中 `-Dmaven.test.skip=true` 跳过了测试
```python
# evaluator.py 第473行
cmd = ["mvn", "clean", "test", "-pl", "gson", "-am", f"-Dtest={baseline_test}", "-Dmaven.test.skip=true"]
#                                                                      ^^^^^^^^^^^^^^^^^^^^^^^^
# 这会跳过测试执行！
```

### 问题2：_measure_coverage 删除了 exec 文件
```python
# evaluator.py 第388-390行
def _measure_coverage(self, target_class: str) -> Optional[CoverageReport]:
    if os.path.exists(self.exec_file):
        os.remove(self.exec_file)  # 删除了覆盖率数据！
```

### 问题3：_run_test 后没有保留 exec 文件
测试运行后的 exec 文件被 _measure_coverage 删除了。

## 5. 修复方案

1. 移除 `-Dmaven.test.skip=true`
2. 不删除 exec 文件，在运行测试前清理
3. 确保 append 模式正确使用

## 6. append 模式验证 ✅

```bash
# 第一次运行 (append=false, 覆盖模式)
exec 大小: 129509 字节

# 第二次运行 (append=true, 追加模式)
exec 大小: 334582 字节 (增大了！)

结论: append=true 可以累积多次测试的覆盖率
```

## 7. 修复后的覆盖率统计流程

```
基准覆盖率:
  1. 删除旧的 exec 文件
  2. 运行原有测试 (append=false)
  3. 解析 exec 文件获取覆盖率

新测试覆盖率:
  1. 删除旧的 exec 文件
  2. 运行原有测试 + 新测试 (append=false, 一次性运行)
  3. 解析 exec 文件获取覆盖率

覆盖率对比:
  新覆盖率 - 基准覆盖率 = 提升量
```

## 8. 关键发现：append=false 问题 ⚠️

**问题现象**：
- 使用 `append=false` 时，exec 文件只包含 Maven 构建过程的类
- 没有 Gson 测试类的覆盖率数据

**原因分析**：
- Maven 运行时会启动多个 JVM 进程
- `append=false` 模式下，每个进程都会覆盖 exec 文件
- 最后一个进程（Maven 构建进程）的数据覆盖了测试进程的数据

**解决方案**：
- 不在 JaCoCo agent 参数中使用 `append=false`
- 改为在运行测试前手动删除旧的 exec 文件
- 让 JaCoCo 默认使用覆盖模式（每次运行自动覆盖旧文件）

**验证结果**：
```bash
# append=false 方式（失败）
exec size: 129939, 无 JsonReader 数据

# 不指定 append 参数（成功）
exec size: 208606, 有 JsonReader 数据
```

## 9. 修复验证 ✅

修复后运行 `get_baseline_coverage`：
```
[→] 获取基准覆盖率（测试类: JsonReaderTest）
  ✓ 基准覆盖率: 行 93.3%, 分支 85.6%

✓ 基准覆盖率获取成功!
  行覆盖率: 93.3%
  分支覆盖率: 85.6%
  覆盖行数: 672/720
```

## 10. 总结

### 根本原因
1. `-Dmaven.test.skip=true` 跳过了测试执行
2. `_measure_coverage` 删除了刚生成的 exec 文件
3. `append=false` 参数导致 Maven 多进程环境下数据被覆盖

### 修复方案
1. 移除 `-Dmaven.test.skip=true` 参数
2. 不在 `_measure_coverage` 中删除 exec 文件
3. 在运行测试前删除旧 exec 文件，不使用 `append=false` 参数
4. 让 JaCoCo 默认使用覆盖模式

## 11. 最终验证结果 ✅

运行完整流程后的覆盖率统计：
```
基准覆盖率: 行 93.3%, 分支 85.6%
新测试后覆盖率: 行 93.5%, 分支 85.8%
覆盖率变化:
  行覆盖率: 93.3% → 93.5% (+0.1%)
  分支覆盖率: 85.6% → 85.8% (+0.2%)
  覆盖行数: 672 → 673 (+1)
```

## 12. 修复的文件

- `evaluation/evaluator.py`
  - `get_baseline_coverage()`: 移除 `-Dmaven.test.skip=true`，移除 `append=false`
  - `_run_test()`: 在运行前删除旧 exec 文件，移除 `append=false`
  - `_measure_coverage()`: 不再删除 exec 文件
