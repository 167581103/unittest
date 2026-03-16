#!/bin/bash

# Set Java 17 as default
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk
export PATH=$JAVA_HOME/bin:$PATH

# Set Maven 3.9.6
export M2_HOME=/opt/maven-new
export PATH=$M2_HOME/bin:$PATH

# Get project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JACOCO_HOME="$PROJECT_ROOT/lib"

export JAVA_TOOL_OPTIONS="-javaagent:$JACOCO_HOME/lib/jacocoagent.jar=destfile=/tmp/gson-jacoco.exec,append=true"

cd $PROJECT_ROOT/data/project/gson

mvn clean test -pl gson -am -Dtest=com.google.gson.stream.JsonReaderTest

unset JAVA_TOOL_OPTIONS

java -jar $JACOCO_HOME/lib/jacococli.jar execinfo /tmp/gson-jacoco.exec | grep gson

java -jar $JACOCO_HOME/lib/jacococli.jar report /tmp/gson-jacoco.exec --classfiles gson/target/classes --sourcefiles gson/src/main/java --html jacoco-report/html
