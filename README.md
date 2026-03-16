# LLM Based Unit Test Generation

UNITTEST
|
|--- rag: Knowledge Collection Module
|--- llm: Unit Test Generation Module
|--- evaluation: Quality evaluation Module
|--- data: Dataset Module
|--- core: Core Module

## 环境安装步骤

### 1. 克隆项目
```bash
git clone https://github.com/167581103/unittest.git
cd unittest
```

### 2. 安装 Java 17
```bash
yum install -y java-17-openjdk java-17-openjdk-devel java-17-openjdk-jmods
```

### 3. 安装 Maven 3.9.6
```bash
cd /opt
wget https://archive.apache.org/dist/maven/maven-3/3.9.6/binaries/apache-maven-3.9.6-bin.tar.gz
tar -xzf apache-maven-3.9.6-bin.tar.gz
mv apache-maven-3.9.6 /opt/maven-new
rm apache-maven-3.9.6-bin.tar.gz
```

### 4. 运行 setup.sh 安装 Jacoco
```bash
cd /data/workspace/unittest
bash setup.sh
```
此脚本会自动下载 Jacoco 0.8.14 到 `lib/` 目录。

### 5. 运行测试
```bash
bash evaluation/run.sh
```

## run.sh 脚本改造说明

`evaluation/run.sh` 脚本进行了以下修改：

1. **Java 环境设置**：添加 Java 17 环境变量
   ```bash
   export JAVA_HOME=/usr/lib/jvm/java-17-openjdk
   export PATH=$JAVA_HOME/bin:$PATH
   ```

2. **Maven 路径配置**：添加 Maven 3.9.6 路径
   ```bash
   export M2_HOME=/opt/maven-new
   export PATH=$M2_HOME/bin:$PATH
   ```

3. **Jacoco 路径修正**：将 Jacoco 路径从 `lib/jacoco-0.8.14` 改为 `lib`
   ```bash
   # 原路径：JACOCO_HOME="$PROJECT_ROOT/lib/jacoco-0.8.14"
   # 新路径：JACOCO_HOME="$PROJECT_ROOT/lib"
   ```

## 环境要求

| 组件 | 版本 | 路径 |
|------|------|------|
| Java | OpenJDK 17 | `/usr/lib/jvm/java-17-openjdk` |
| Maven | 3.9.6 | `/opt/maven-new` |
| Jacoco | 0.8.14 | `lib/lib/` |

## 工作流程

1. 离线嵌入实现，这一步做好分块、向量模型选取、向量数据库选型；
2. 在线RAG实现，这一步做好检索策略和重排策略；
3. Prompt Builder实现，这个好做，jinja渲染就行；
4. LLM Generator，这个好做，Prompt Template设计好了，只需要抽取一下生成的测试代码就好；
5. 执行和验证，这一块需要做好jacoco集成，判断测试覆盖率，同时可以引入一些其他大模型来评估生成测试的质量，然后生成一些Critic再传给RAG模块去进行检索相关的内容（这里可以做Agentic RAG，让模型决策RAG方案），然后把Critic和相关的内容再传给模型让模型去优化代码质量。
6. 当验证成功，即覆盖率等等都达到要求了，测试代码也可正常执行了，就可以交付结果Result。
