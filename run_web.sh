#!/bin/bash
# Start the UT-Gen Dashboard web server

cd /data/workspace/unittest

# Set up environment
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk
export PATH=$JAVA_HOME/bin:$PATH
export M2_HOME=/opt/maven-new
export PATH=$M2_HOME/bin:$PATH

echo "============================================"
echo "  UT-Gen Dashboard"
echo "  http://localhost:8081"
echo "============================================"

python -m uvicorn web.server:app --host 0.0.0.0 --port 8081 --reload
