# Evaluation Module

评估模块负责两大工作：执行和评估。

1. 执行模块：执行模块会通过maven构建项目，并在项目中执行模型输出的测试内容。测试执行结果会被收集为完整报告用于反馈。
2. 评估模块：评估模块通过集成jacoco工具，对生成的测试进行覆盖率等质量的评估。评估结果会被收集为完整报告用于反馈。

评估模块会将所有收集好的与测试相关的数据整合为一份报告，该报告将在分析后反馈给模型，用于进一步改善测试质量。

```bash
mvn test -pl gson -Dtest=com.google.gson.stream.JsonReaderTest#testSkipArray
```

```bash
export JAVA_TOOL_OPTIONS="-javaagent:/home/juu/unittest/evaluation/jacoco-0.8.14/lib/jacocoagent.jar=destfile=/tmp/gson-jacoco.exec,append=true"

cd /home/juu/unittest/data/project/gson

mvn clean test -pl gson -am -Dtest=com.google.gson.stream.JsonReaderTest

unset JAVA_TOOL_OPTIONS

java -jar /home/juu/unittest/evaluation/jacoco-0.8.14/lib/jacococli.jar execinfo /tmp/gson-jacoco.exec | grep gson

java -jar /home/juu/unittest/evaluation/jacoco-0.8.14/lib/jacococli.jar report /tmp/gson-jacoco.exec --classfiles gson/target/classes --sourcefiles gson/src/main/java --html jacoco-report/html
```