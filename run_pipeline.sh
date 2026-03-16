#!/bin/bash
# 运行完整测试生成Pipeline

cd /data/workspace/unittest

# 设置环境变量
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk
export PATH=$JAVA_HOME/bin:$PATH
export M2_HOME=/opt/maven-new
export PATH=$M2_HOME/bin:$PATH

# 运行pipeline
python core/generate_test_pipeline.py
