"""
极简Evaluation模块 - 测试执行与覆盖率评估

功能：
1. 执行生成的单元测试
2. 收集测试结果
3. 评估代码覆盖率
4. 生成评估报告
"""

import os
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class TestResult:
    """测试结果"""
    test_class: str
    test_method: str
    passed: bool
    duration_ms: float
    error_message: str = ""


@dataclass
class MethodCoverage:
    """单个方法的覆盖率"""
    method_name: str
    line_coverage: float
    branch_coverage: float
    covered_lines: int
    total_lines: int
    covered_branches: int = 0
    total_branches: int = 0
    desc: str = ""  # JVM 描述符，用于区分重载方法，例如 (Ljava/lang/Number;)Lcom/google/gson/stream/JsonWriter;


@dataclass
class CoverageReport:
    """覆盖率报告"""
    class_name: str
    line_coverage: float
    branch_coverage: float
    method_coverage: float
    covered_lines: int
    total_lines: int
    method_coverages: List[MethodCoverage] = None  # 方法级别覆盖率
    
    def __post_init__(self):
        if self.method_coverages is None:
            self.method_coverages = []
    
    def get_method_coverage(self, method_name: str) -> Optional[MethodCoverage]:
        """获取指定方法的覆盖率（聚合所有同名重载方法）"""
        matches = [mc for mc in self.method_coverages if mc.method_name == method_name]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        # Aggregate all overloads: sum covered/total lines and branches
        total_covered = sum(mc.covered_lines for mc in matches)
        total_lines = sum(mc.total_lines for mc in matches)
        total_covered_branches = sum(mc.covered_branches for mc in matches)
        total_branches = sum(mc.total_branches for mc in matches)
        line_cov = (total_covered / total_lines * 100) if total_lines > 0 else 0.0
        branch_cov = (total_covered_branches / total_branches * 100) if total_branches > 0 else 0.0
        return MethodCoverage(
            method_name=method_name,
            line_coverage=line_cov,
            branch_coverage=branch_cov,
            covered_lines=total_covered,
            total_lines=total_lines,
            covered_branches=total_covered_branches,
            total_branches=total_branches,
        )


@dataclass
class EvaluationReport:
    """完整评估报告"""
    test_file: str
    target_class: str
    target_method: str
    test_results: List[TestResult]
    coverage: Optional[CoverageReport]
    baseline_coverage: Optional[CoverageReport] = None
    coverage_change: Optional[Dict] = None
    compilation_success: bool = True
    errors: List[str] = None
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class TestEvaluator:
    """测试评估器"""
    
    def __init__(self, project_dir: str, jacoco_home: Optional[str] = None):
        """
        初始化评估器
        
        Args:
            project_dir: Maven项目根目录
            jacoco_home: JaCoCo工具路径
        """
        self.project_dir = project_dir
        self.jacoco_home = jacoco_home or "/home/juu/unittest/lib/jacoco-0.8.14"
        self.exec_file = "/tmp/gson-jacoco.exec"
        self._actual_test_class = None  # 实际使用的测试类名
    
    def evaluate(
        self,
        test_file: str,
        test_class: str,
        target_class: str,
        target_method: str,
        baseline_test: str = None
    ) -> EvaluationReport:
        """
        评估生成的测试
        
        Args:
            test_file: 测试文件路径
            test_class: 测试类全名（如 com.google.gson.stream.JsonReaderTest）
            target_class: 被测试的目标类
            target_method: 被测试的目标方法
            baseline_test: 用于获取基准覆盖率的测试类名（默认从target_class推导）
        """
        errors = []
        
        # 推导基准测试类名
        if baseline_test is None:
            class_name = target_class.split(".")[-1]
            baseline_test = f"{class_name}Test"
        
    # 0. 先清理所有残留的 Generated 测试文件，确保环境干净
        print(f"[→] 清理残留的 Generated 测试文件...")
        self._cleanup_old_generated_tests()

        # 1. 获取基准覆盖率
        baseline_coverage = self.get_baseline_coverage(target_class, baseline_test)
        
        # 2. 复制测试文件到项目
        print(f"\n[→] 复制测试文件: {test_file}")
        copy_success = self._copy_test_file(test_file, test_class)
        if not copy_success:
            errors.append("复制测试文件失败")
            return EvaluationReport(
                test_file=test_file,
                target_class=target_class,
                target_method=target_method,
                test_results=[],
                coverage=None,
                baseline_coverage=baseline_coverage,
                compilation_success=False,
                errors=errors
            )
        
        actual_test_class = self._actual_test_class or test_class

        # 3. 编译测试
        print(f"[→] 编译测试: {actual_test_class}")
        compile_success = self._compile_test(actual_test_class)
        if not compile_success:
            errors.append("编译失败")
            return EvaluationReport(
                test_file=test_file,
                target_class=target_class,
                target_method=target_method,
                test_results=[],
                coverage=None,
                baseline_coverage=baseline_coverage,
                compilation_success=False,
                errors=errors
            )

        # 4. 运行测试（原有测试 + 新测试）
        baseline_full_class = ".".join(target_class.split(".")[:-1] + [baseline_test])
        if "." in test_class:
            baseline_full_class = ".".join(test_class.split(".")[:-1] + [baseline_test])
        test_classes_to_run = [baseline_full_class, actual_test_class]
        print(f"[→] 运行测试: {test_classes_to_run}")
        test_results = self._run_test(test_classes_to_run)
        
        # 5. 评估覆盖率
        print(f"[→] 评估覆盖率: {target_class}")
        coverage = self._get_coverage_from_exec(self.exec_file, target_class)
        
        # 6. 对比覆盖率变化
        coverage_change = None
        if baseline_coverage and coverage:
            coverage_change = self.compare_coverage(baseline_coverage, coverage)
            print(f"\n[→] 覆盖率变化:")
            print(f"  行覆盖率: {baseline_coverage.line_coverage:.1f}% → {coverage.line_coverage:.1f}% ({coverage_change['line_coverage_change']:+.1f}%)")
            print(f"  分支覆盖率: {baseline_coverage.branch_coverage:.1f}% → {coverage.branch_coverage:.1f}% ({coverage_change['branch_coverage_change']:+.1f}%)")
            print(f"  覆盖行数: {baseline_coverage.covered_lines} → {coverage.covered_lines} ({coverage_change['line_change']:+d})")
        
        return EvaluationReport(
            test_file=test_file,
            target_class=target_class,
            target_method=target_method,
            test_results=test_results,
            coverage=coverage,
            baseline_coverage=baseline_coverage,
            coverage_change=coverage_change,
            compilation_success=True,
            errors=errors
        )
    
    def _copy_test_file(self, test_file: str, test_class: str) -> bool:
        """复制测试文件到项目测试目录"""
        try:
            # 清理旧的生成文件，避免编译冲突
            self._cleanup_old_generated_tests()
            
            # 使用唯一的类名（添加Generated后缀和时间戳避免冲突）
            import time
            timestamp = int(time.time())
            
            # 解析包名和类名
            if "." in test_class:
                package = ".".join(test_class.split(".")[:-1])
                original_class_name = test_class.split(".")[-1]
            else:
                package = ""  # 默认包
                original_class_name = test_class
            
            unique_class_name = f"{original_class_name}Generated{timestamp}"
            
            # 构建目标路径 - 检测是否是多模块项目
            gson_module_path = os.path.join(self.project_dir, "gson")
            if os.path.exists(gson_module_path):
                # 多模块项目，使用gson子模块的路径
                base_dir = gson_module_path
            else:
                # 单模块项目
                base_dir = self.project_dir
            
            if package:
                package_path = package.replace(".", "/")
                target_dir = Path(base_dir) / "src/test/java" / package_path
            else:
                target_dir = Path(base_dir) / "src/test/java"
            target_dir.mkdir(parents=True, exist_ok=True)
            
            # 读取并修改内容
            target_file = target_dir / f"{unique_class_name}.java"
            with open(test_file, 'r', encoding='utf-8') as src:
                content = src.read()
            
            # 提取实际的测试类名（从生成的文件中）- 不使用正则
            actual_class_name = original_class_name
            for line in content.split('\n'):
                line = line.strip()
                if line.startswith('public class ') and '{' in line:
                    # 格式: public class ClassName {
                    actual_class_name = line.split('public class ')[1].split('{')[0].strip()
                    print(f"  ✓ 检测到实际类名: {actual_class_name}")
                    break
            else:
                print(f"  ! 未检测到类名，使用预期类名: {original_class_name}")
            
            # 替换类名（使用实际提取的类名）
            content = content.replace(f"public class {actual_class_name}", 
                                     f"public class {unique_class_name}")
            
            # 确保package声明正确（只在有包名时添加）
            if package and f"package {package};" not in content:
                content = f"package {package};\n\n{content}"
            
            with open(target_file, 'w', encoding='utf-8') as dst:
                dst.write(content)
            
            # 保存实际使用的类名供后续使用
            self._actual_test_class = f"{package}.{unique_class_name}" if package else unique_class_name
            
            print(f"  ✓ 已复制到: {target_file}")
            print(f"  ✓ 使用类名: {unique_class_name}")
            return True
        except Exception as e:
            print(f"  ✗ 复制失败: {e}")
            return False
    
    def _cleanup_old_generated_tests(self):
        """清理旧的生成测试文件，避免编译冲突"""
        try:
            import glob
            # 检测是否是多模块项目
            gson_module_path = os.path.join(self.project_dir, "gson")
            if os.path.exists(gson_module_path):
                # 多模块项目
                test_dirs = [
                    os.path.join(gson_module_path, "src/test/java"),
                    os.path.join(self.project_dir, "src/test/java")
                ]
            else:
                # 单模块项目
                test_dirs = [os.path.join(self.project_dir, "src/test/java")]

            # 兼容两类历史产物：
            # 1) *Generated*.java（当前策略）
            # 2) *_Test.java（早期批量实验遗留文件，可能触发 -Werror）
            patterns = ["*Generated*.java", "*_Test.java"]
            deleted_count = 0

            for test_dir in test_dirs:
                if not os.path.exists(test_dir):
                    continue

                for name_pattern in patterns:
                    pattern = os.path.join(test_dir, "**", name_pattern)
                    old_files = glob.glob(pattern, recursive=True)
                    for old_file in old_files:
                        try:
                            os.remove(old_file)
                            deleted_count += 1
                        except Exception:
                            pass  # 忽略删除失败

            if deleted_count > 0:
                print(f"  ✓ 已清理旧生成测试文件: {deleted_count} 个")
        except Exception as e:
            print(f"  ! 清理旧文件失败（继续）: {e}")
    
    def _compile_test(self, test_class: str) -> bool:
        """编译测试文件"""
        success, _ = self._compile_test_with_output(test_class)
        return success

    def _compile_test_with_output(self, test_class: str) -> tuple:
        """编译测试文件，返回 (success, raw_output)"""
        # 确保在项目根目录执行命令，避免增量编译问题
        project_root = self.project_dir if os.path.exists(os.path.join(self.project_dir, "pom.xml")) else os.path.dirname(self.project_dir)
        
        # 检查是否是多模块项目
        gson_module_path = os.path.join(project_root, "gson")
        if os.path.exists(gson_module_path):
            # 多模块项目，跳过有问题的test-jpms模块，只编译gson模块
            cmd = [
                "mvn", "compile", "test-compile",
                "-pl", "gson",
                "-am",
                "-DskipTests",
                "-Dmaven.compiler.failOnWarning=false"
            ]
        else:
            # 单模块项目
            cmd = [
                "mvn", "compile", "test-compile",
                "-DskipTests",
                "-Dmaven.compiler.failOnWarning=false"
            ]
        
        try:
            # Set Java 17 and Maven environment (same as get_baseline_coverage)
            env = os.environ.copy()
            env["JAVA_HOME"] = "/usr/lib/jvm/java-17-openjdk"
            env["PATH"] = f"/usr/lib/jvm/java-17-openjdk/bin:/opt/maven-new/bin:{env.get('PATH', '')}"
            env["M2_HOME"] = "/opt/maven-new"
            result = subprocess.run(
                cmd,
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=180,
                env=env,
            )
            combined_output = result.stdout + result.stderr
            success = result.returncode == 0
            if not success:
                # 提取编译错误信息（放宽关键词：只要是 [ERROR] .java:[行,列] 格式就展示）
                if "BUILD FAILURE" in combined_output or "COMPILATION ERROR" in combined_output:
                    import re as _re
                    # 任何形如 [ERROR] /xxx.java:[12,34] msg 的行都视为编译错误
                    error_lines = []
                    for line in combined_output.split('\n'):
                        line_stripped = line.strip()
                        if _re.search(r'\[ERROR\].*\.java:\[\d+,\d+\]', line_stripped):
                            error_lines.append(line_stripped)
                        elif 'ERROR' in line_stripped and any(kw in line_stripped for kw in (
                            'cannot find symbol', 'does not exist', 'incompatible types',
                            'unreported exception', 'illegal character', 'compilation problem',
                            'reached end of file', "';' expected", 'class, interface',
                            'not a statement', 'unclosed', "'{' expected", "'}' expected",
                            'method in class', 'is not abstract'
                        )):
                            error_lines.append(line_stripped)

                    if error_lines:
                        print(f"  ✗ 编译错误:")
                        for error in error_lines[:5]:  # 只显示前5个错误
                            print(f"    {error}")
                        if len(error_lines) > 5:
                            print(f"    ... 还有 {len(error_lines) - 5} 个错误")
                    else:
                        print(f"  ✗ 编译失败（无详细错误信息）")
                else:
                    print(f"  ✗ 编译失败")
            else:
                print(f"  ✓ 编译成功")
            return success, combined_output
        except subprocess.TimeoutExpired:
            print(f"  ✗ 编译超时")
            return False, "Compile timeout"
        except Exception as e:
            print(f"  ✗ 编译异常: {e}")
            return False, str(e)
    
    def _run_test(self, test_classes: List[str]) -> List[TestResult]:
        """运行测试（支持多个测试类）

        Args:
            test_classes: 测试类全名列表
        """
        project_root = self.project_dir if os.path.exists(os.path.join(self.project_dir, "pom.xml")) else os.path.dirname(self.project_dir)

        results = []
        gson_module_path = os.path.join(project_root, "gson")
        
        # 在运行测试前删除旧的 exec 文件
        if os.path.exists(self.exec_file):
            os.remove(self.exec_file)
        
        # 一次性运行所有测试类，累积覆盖率
        test_class_names = [tc.split(".")[-1] for tc in test_classes]
        test_pattern = ",".join(test_class_names)
        
        # 使用 JAVA_TOOL_OPTIONS 传递 JaCoCo agent（不指定 append，让 JaCoCo 默认覆盖）
        jacoco_agent = f"-javaagent:{self.jacoco_home}/lib/jacocoagent.jar=destfile={self.exec_file}"
        env = os.environ.copy()
        env["JAVA_TOOL_OPTIONS"] = jacoco_agent
        env["JAVA_HOME"] = "/usr/lib/jvm/java-17-openjdk"
        env["PATH"] = f"/usr/lib/jvm/java-17-openjdk/bin:/opt/maven-new/bin:{env.get('PATH', '')}"
        env["M2_HOME"] = "/opt/maven-new"
        
        if os.path.exists(gson_module_path):
            cmd = [
                "mvn", "test",
                "-pl", "gson",
                "-am",
                f"-Dtest={test_pattern}",
                "-Dmaven.compiler.failOnWarning=false",
                "-Dmaven.test.failure.ignore=true",  # Don't abort on test assertion failures
            ]
        else:
            cmd = ["mvn", "test", f"-Dtest={test_pattern}",
                   "-Dmaven.compiler.failOnWarning=false",
                   "-Dmaven.test.failure.ignore=true"]
        
        try:
            result = subprocess.run(cmd, cwd=project_root, text=True, timeout=120, env=env,
                                    capture_output=True)
            
            if result.returncode == 0:
                print(f"  ✓ 测试运行完成")
            else:
                print(f"  ✗ 测试运行失败 (返回码: {result.returncode})")
                # Print key lines for diagnosis
                combined = result.stdout + result.stderr
                for line in combined.split('\n'):
                    if any(kw in line for kw in ['ERROR', 'FAILURE', 'Tests run', 'BUILD']):
                        print(f"    {line.strip()}")
            
        except subprocess.TimeoutExpired:
            print(f"  ✗ 测试运行超时")
        except Exception as e:
            print(f"  ✗ 测试运行异常: {e}")
        
        return results
    
    def _measure_coverage(self, target_class: str) -> Optional[CoverageReport]:
        """Measure code coverage from exec file generated by _run_test."""
        return self._get_coverage_from_exec(self.exec_file, target_class)
    
    def get_baseline_coverage(self, target_class: str, baseline_test: str = "JsonReaderTest") -> Optional[CoverageReport]:
        """获取基准覆盖率（不包含新生成的测试）
        
        Args:
            target_class: 目标类全名
            baseline_test: 用于获取基准覆盖率的测试类名
        """
        print(f"[→] 获取基准覆盖率（测试类: {baseline_test}）")

        # ★ 关键：先清理上一次 pipeline 残留的 *Generated* 测试文件，
        # 否则它们会让基准 Maven 构建直接失败。
        self._cleanup_old_generated_tests()

        project_root = self.project_dir if os.path.exists(os.path.join(self.project_dir, "pom.xml")) else os.path.dirname(self.project_dir)
        baseline_exec = "/tmp/baseline.exec"
        
        # 删除旧的 exec 文件
        if os.path.exists(baseline_exec):
            os.remove(baseline_exec)
        
        # 使用 JAVA_TOOL_OPTIONS 传递 JaCoCo agent（不指定 append）
        jacoco_agent = f"-javaagent:{self.jacoco_home}/lib/jacocoagent.jar=destfile={baseline_exec}"
        env = os.environ.copy()
        env["JAVA_TOOL_OPTIONS"] = jacoco_agent
        # 设置 Java 17 环境变量（gson 项目需要 Java 17）
        env["JAVA_HOME"] = "/usr/lib/jvm/java-17-openjdk"
        env["M2_HOME"] = "/opt/maven-new"
        env["PATH"] = f"/usr/lib/jvm/java-17-openjdk/bin:/opt/maven-new/bin:{env.get('PATH', '')}"
        
        # 运行基准测试
        gson_module_path = os.path.join(project_root, "gson")
        if os.path.exists(gson_module_path):
            cmd = [
                "mvn", "test",
                "-pl", "gson",
                "-am",
                f"-Dtest={baseline_test}",
                "-DfailIfNoTests=false",
                "-Dmaven.compiler.failOnWarning=false"
            ]
        else:
            cmd = ["mvn", "test", f"-Dtest={baseline_test}", "-DfailIfNoTests=false", "-Dmaven.compiler.failOnWarning=false"]
        
        try:
            # 不使用 capture_output，让 Maven 输出显示出来以便诊断问题
            result = subprocess.run(cmd, cwd=project_root, timeout=180, env=env,
                                       capture_output=True, text=True)
            
            # 检查 Maven 返回码
            if result.returncode != 0:
                print(f"  ✗ Maven 构建失败，返回码: {result.returncode}")
                # 输出关键错误信息帮助诊断
                combined = result.stdout + result.stderr
                for line in combined.split('\n'):
                    if any(kw in line for kw in ['ERROR', 'FAILURE', 'error:', 'cannot find', 'does not exist']):
                        print(f"    {line.strip()}")
                return None
            
            if not os.path.exists(baseline_exec):
                print(f"  ✗ 基准覆盖率文件未生成: {baseline_exec}")
                return None
            
            # 检查 exec 文件大小
            exec_size = os.path.getsize(baseline_exec)
            print(f"  ✓ 基准覆盖率文件大小: {exec_size} bytes")
            
            # 从exec文件读取覆盖率
            coverage = self._get_coverage_from_exec(baseline_exec, target_class)
            if coverage:
                print(f"  ✓ 基准覆盖率: 行 {coverage.line_coverage:.1f}%, 分支 {coverage.branch_coverage:.1f}%")
            else:
                print(f"  ✗ 无法从 exec 文件解析覆盖率")
            return coverage
            
        except Exception as e:
            print(f"  ✗ 获取基准覆盖率失败: {e}")
            return None
    
    def _get_coverage_from_exec(self, exec_file: str, target_class: str) -> Optional[CoverageReport]:
        """从JaCoCo exec文件获取覆盖率（包含方法级别）"""
        try:
            project_root = self.project_dir if os.path.exists(os.path.join(self.project_dir, "pom.xml")) else os.path.dirname(self.project_dir)
            gson_module_path = os.path.join(project_root, "gson")
            
            if os.path.exists(gson_module_path):
                classfiles_path = os.path.join(gson_module_path, "target/classes")
            else:
                classfiles_path = os.path.join(self.project_dir, "target/classes")
            
            # 生成XML报告（支持方法级别覆盖率）
            xml_file = "/tmp/coverage.xml"
            cmd = [
                "java", "-jar",
                f"{self.jacoco_home}/lib/jacococli.jar",
                "report", exec_file,
                "--classfiles", classfiles_path,
                "--xml", xml_file
            ]
            
            subprocess.run(cmd, capture_output=True, timeout=60)
            
            # 解析XML报告
            if not os.path.exists(xml_file):
                return None
            
            return self._parse_xml_coverage(xml_file, target_class)
            
        except Exception as e:
            print(f"  ✗ 覆盖率解析失败: {e}")
            return None
    
    def _parse_xml_coverage(self, xml_file: str, target_class: str) -> Optional[CoverageReport]:
        """解析JaCoCo XML报告（支持方法级别）"""
        import xml.etree.ElementTree as ET
        
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
            
            class_name = target_class.split(".")[-1]
            # XML中class的name格式为: com/google/gson/stream/JsonReader
            class_path = target_class.replace(".", "/")
            
            # 查找目标类
            for package in root.findall(".//package"):
                for cls in package.findall("class"):
                    cls_name = cls.get("name", "")
                    # 匹配: 完整路径 或 简单类名（排除内部类）
                    if cls_name == class_path or (cls_name.endswith("/" + class_name) and "$" not in cls_name):
                        # 解析类级别覆盖率
                        class_counters = {c.get("type"): c for c in cls.findall("counter")}
                        
                        line_missed = int(class_counters.get("LINE", {}).get("missed", 0))
                        line_covered = int(class_counters.get("LINE", {}).get("covered", 0))
                        branch_missed = int(class_counters.get("BRANCH", {}).get("missed", 0))
                        branch_covered = int(class_counters.get("BRANCH", {}).get("covered", 0))
                        method_missed = int(class_counters.get("METHOD", {}).get("missed", 0))
                        method_covered = int(class_counters.get("METHOD", {}).get("covered", 0))
                        
                        total_lines = line_missed + line_covered
                        total_branches = branch_missed + branch_covered
                        total_methods = method_missed + method_covered
                        
                        # 解析方法级别覆盖率
                        method_coverages = []
                        for method in cls.findall("method"):
                            method_name = method.get("name", "")
                            method_desc = method.get("desc", "")
                            method_counters = {c.get("type"): c for c in method.findall("counter")}
                            
                            m_line_missed = int(method_counters.get("LINE", {}).get("missed", 0))
                            m_line_covered = int(method_counters.get("LINE", {}).get("covered", 0))
                            m_branch_missed = int(method_counters.get("BRANCH", {}).get("missed", 0))
                            m_branch_covered = int(method_counters.get("BRANCH", {}).get("covered", 0))
                            
                            m_total_lines = m_line_missed + m_line_covered
                            m_total_branches = m_branch_missed + m_branch_covered
                            
                            if m_total_lines > 0:  # 只记录有代码行的方法
                                method_coverages.append(MethodCoverage(
                                    method_name=method_name,
                                    line_coverage=(m_line_covered / m_total_lines * 100) if m_total_lines > 0 else 0,
                                    branch_coverage=(m_branch_covered / m_total_branches * 100) if m_total_branches > 0 else 0,
                                    covered_lines=m_line_covered,
                                    total_lines=m_total_lines,
                                    covered_branches=m_branch_covered,
                                    total_branches=m_total_branches,
                                    desc=method_desc,
                                ))
                        
                        return CoverageReport(
                            class_name=target_class,
                            line_coverage=(line_covered / total_lines * 100) if total_lines > 0 else 0,
                            branch_coverage=(branch_covered / total_branches * 100) if total_branches > 0 else 0,
                            method_coverage=(method_covered / total_methods * 100) if total_methods > 0 else 0,
                            covered_lines=line_covered,
                            total_lines=total_lines,
                            method_coverages=method_coverages
                        )
            
            return None
            
        except Exception as e:
            print(f"  ✗ XML解析失败: {e}")
            return None
    
    def compare_coverage(self, baseline: CoverageReport, new: CoverageReport) -> Dict:
        """对比覆盖率变化"""
        return {
            "line_coverage_change": new.line_coverage - baseline.line_coverage,
            "branch_coverage_change": new.branch_coverage - baseline.branch_coverage,
            "line_change": new.covered_lines - baseline.covered_lines,
            "baseline": baseline,
            "new": new
        }
    
    def find_low_coverage_methods(self, coverage: CoverageReport, threshold: float = 80.0) -> List[MethodCoverage]:
        """查找覆盖率低于阈值的方法
        
        Args:
            coverage: 覆盖率报告
            threshold: 覆盖率阈值（默认80%）
        
        Returns:
            按覆盖率排序的方法列表
        """
        low_cov = [mc for mc in coverage.method_coverages if mc.line_coverage < threshold]
        return sorted(low_cov, key=lambda x: x.line_coverage)


def _short_desc(desc: str) -> str:
    """将 JVM 方法描述符简化为人类可读签名。
    
    示例：
        (Ljava/lang/Number;)Lcom/google/gson/stream/JsonWriter;  -> (Number)
        (D)Lcom/google/gson/stream/JsonWriter;                   -> (double)
        (ZLjava/lang/String;)V                                   -> (boolean, String)
        ()V                                                      -> ()
    """
    if not desc or "(" not in desc or ")" not in desc:
        return ""
    params_raw = desc[desc.index("(") + 1:desc.index(")")]
    prims = {"Z": "boolean", "B": "byte", "C": "char", "S": "short",
             "I": "int", "J": "long", "F": "float", "D": "double", "V": "void"}
    out = []
    i = 0
    while i < len(params_raw):
        c = params_raw[i]
        arr = ""
        while c == "[":
            arr += "[]"
            i += 1
            c = params_raw[i]
        if c in prims:
            out.append(prims[c] + arr)
            i += 1
        elif c == "L":
            end = params_raw.index(";", i)
            cls = params_raw[i + 1:end].split("/")[-1]
            out.append(cls + arr)
            i = end + 1
        else:
            i += 1
    return "(" + ", ".join(out) + ")"


def print_report(report: EvaluationReport, show_method_coverage: bool = True):
    """打印评估报告
    
    Args:
        report: 评估报告
        show_method_coverage: 是否显示方法级别覆盖率
    """
    print("\n" + "=" * 60)
    print("评估报告")
    print("=" * 60)
    
    print(f"\n测试文件: {report.test_file}")
    print(f"目标类: {report.target_class}")
    print(f"目标方法: {report.target_method}")
    print(f"编译成功: {report.compilation_success}")
    
    if report.errors:
        print(f"\n错误:")
        for error in report.errors:
            print(f"  - {error}")
    
    if report.test_results:
        print(f"\n测试结果:")
        for result in report.test_results:
            status = "✓" if result.passed else "✗"
            print(f"  {status} {result.test_method}")
    
    if report.coverage:
        print(f"\n类级覆盖率:")
        print(f"  行覆盖率: {report.coverage.line_coverage:.1f}% ({report.coverage.covered_lines}/{report.coverage.total_lines})")
        print(f"  分支覆盖率: {report.coverage.branch_coverage:.1f}%")
        print(f"  方法覆盖率: {report.coverage.method_coverage:.1f}%")
        
        if show_method_coverage and report.coverage.method_coverages:
            # Show target method coverage prominently (with before/after delta if baseline exists)
            target_mc = report.coverage.get_method_coverage(report.target_method)
            baseline_target_mc = (
                report.baseline_coverage.get_method_coverage(report.target_method)
                if report.baseline_coverage else None
            )
            if target_mc:
                print(f"\n  ★ 目标方法 [{report.target_method}]:")
                if baseline_target_mc:
                    line_delta = target_mc.line_coverage - baseline_target_mc.line_coverage
                    branch_delta = target_mc.branch_coverage - baseline_target_mc.branch_coverage
                    lines_delta = target_mc.covered_lines - baseline_target_mc.covered_lines
                    branches_delta = target_mc.covered_branches - baseline_target_mc.covered_branches
                    print(f"    行覆盖率:   {baseline_target_mc.line_coverage:.1f}% → {target_mc.line_coverage:.1f}% ({line_delta:+.1f}%)  "
                          f"[{baseline_target_mc.covered_lines}/{target_mc.total_lines} → {target_mc.covered_lines}/{target_mc.total_lines}, {lines_delta:+d} 行]")
                    print(f"    分支覆盖率: {baseline_target_mc.branch_coverage:.1f}% → {target_mc.branch_coverage:.1f}% ({branch_delta:+.1f}%)  "
                          f"[{baseline_target_mc.covered_branches}/{target_mc.total_branches} → {target_mc.covered_branches}/{target_mc.total_branches}, {branches_delta:+d} 分支]")
                else:
                    print(f"    行覆盖率:   {target_mc.line_coverage:.1f}% ({target_mc.covered_lines}/{target_mc.total_lines})")
                    print(f"    分支覆盖率: {target_mc.branch_coverage:.1f}% ({target_mc.covered_branches}/{target_mc.total_branches})")

            # Show per-method deltas (methods whose coverage actually changed)
            if report.baseline_coverage and report.baseline_coverage.method_coverages:
                # 用 (name, desc) 作为唯一 key，正确区分重载方法
                baseline_map = {(mc.method_name, mc.desc): mc for mc in report.baseline_coverage.method_coverages}
                changed = []
                for mc in report.coverage.method_coverages:
                    base = baseline_map.get((mc.method_name, mc.desc))
                    if base is None:
                        # 方法在 baseline 中不存在（新增方法），跳过或标记
                        continue
                    line_d = mc.line_coverage - base.line_coverage
                    branch_d = mc.branch_coverage - base.branch_coverage
                    if abs(line_d) > 1e-6 or abs(branch_d) > 1e-6:
                        changed.append((mc, base, line_d, branch_d))
                if changed:
                    print(f"\n  方法级覆盖率变化 ({len(changed)} 个方法有变化):")
                    for mc, base, line_d, branch_d in sorted(changed, key=lambda x: -x[2]):
                        marker = " ★" if mc.method_name == report.target_method else ""
                        sig = _short_desc(mc.desc)
                        label = f"{mc.method_name}{sig}"
                        print(f"    · {label:50s} line: {base.line_coverage:.0f}% → {mc.line_coverage:.0f}% ({line_d:+.1f}%)  "
                              f"branch: {base.branch_coverage:.0f}% → {mc.branch_coverage:.0f}% ({branch_d:+.1f}%){marker}")
                else:
                    print(f"\n  方法级覆盖率变化: 无方法发生变化")

            # Show full method-level panorama
            print(f"\n  方法级覆盖率全景 ({len(report.coverage.method_coverages)} 个方法):")
            # Sort: low coverage first
            sorted_methods = sorted(
                report.coverage.method_coverages,
                key=lambda m: (m.line_coverage, m.branch_coverage)
            )
            for mc in sorted_methods:
                # Mark target method
                marker = " ★" if mc.method_name == report.target_method else ""
                # Color indicator
                if mc.line_coverage >= 100:
                    icon = "●"
                elif mc.line_coverage >= 80:
                    icon = "◐"
                else:
                    icon = "○"
                branch_str = f"  branch={mc.branch_coverage:.0f}% ({mc.covered_branches}/{mc.total_branches})" if mc.total_branches > 0 else ""
                sig = _short_desc(mc.desc)
                label = f"{mc.method_name}{sig}"
                print(f"    {icon} {label:50s} line={mc.covered_lines}/{mc.total_lines} ({mc.line_coverage:.0f}%){branch_str}{marker}")
    
    print("\n" + "=" * 60)


# ============ 主程序 ============

if __name__ == "__main__":
    # 示例：评估生成的测试
    evaluator = TestEvaluator(
        project_dir="/home/juu/unittest/data/project/gson",
        jacoco_home="/home/juu/unittest/lib/jacoco-0.8.14"
    )
    
    report = evaluator.evaluate(
        test_file="/tmp/generated_tests/JsonReader_skipValue_Test.java",
        test_class="com.google.gson.stream.JsonReaderTest",
        target_class="com.google.gson.stream.JsonReader",
        target_method="skipValue"
    )
    
    print_report(report)
