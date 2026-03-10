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
class CoverageReport:
    """覆盖率报告"""
    class_name: str
    line_coverage: float
    branch_coverage: float
    method_coverage: float
    covered_lines: int
    total_lines: int


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
        
        # 4. 运行测试
        print(f"[→] 运行测试: {actual_test_class}")
        test_results = self._run_test(actual_test_class)
        
        # 5. 评估覆盖率
        print(f"[→] 评估覆盖率: {target_class}")
        coverage = self._measure_coverage(target_class)
        
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
            
            for test_dir in test_dirs:
                if os.path.exists(test_dir):
                    # 查找并删除所有 *Generated*.java 文件
                    pattern = os.path.join(test_dir, "**", "*Generated*.java")
                    old_files = glob.glob(pattern, recursive=True)
                    for old_file in old_files:
                        try:
                            os.remove(old_file)
                        except:
                            pass  # 忽略删除失败
        except Exception as e:
            print(f"  ! 清理旧文件失败（继续）: {e}")
    
    def _compile_test(self, test_class: str) -> bool:
        """编译测试文件"""
        # 确保在项目根目录执行命令，避免增量编译问题
        project_root = self.project_dir if os.path.exists(os.path.join(self.project_dir, "pom.xml")) else os.path.dirname(self.project_dir)
        
        # 检查是否是多模块项目
        gson_module_path = os.path.join(project_root, "gson")
        if os.path.exists(gson_module_path):
            # 多模块项目，只编译gson模块（不使用clean避免删除主代码）
            cmd = [
                "mvn", "compile", "test-compile",
                "-pl", "gson",
                "-am",
                "-DskipTests"
            ]
        else:
            # 单模块项目
            cmd = [
                "mvn", "compile", "test-compile",
                "-DskipTests"
            ]
        
        try:
            result = subprocess.run(
                cmd,
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=180
            )
            success = result.returncode == 0
            if not success:
                # 提取编译错误信息
                combined_output = result.stdout + result.stderr
                if "BUILD FAILURE" in combined_output:
                    # 查找具体的错误行
                    error_lines = []
                    for line in combined_output.split('\n'):
                        if 'ERROR' in line and ('cannot find symbol' in line or 
                                                'package.*does not exist' in line or 
                                                'compilation problem' in line):
                            error_lines.append(line.strip())
                    
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
            return success
        except subprocess.TimeoutExpired:
            print(f"  ✗ 编译超时")
            return False
        except Exception as e:
            print(f"  ✗ 编译异常: {e}")
            return False
    
    def _run_test(self, test_class: str) -> List[TestResult]:
        """运行测试"""
        project_root = self.project_dir if os.path.exists(os.path.join(self.project_dir, "pom.xml")) else os.path.dirname(self.project_dir)
        
        jacoco_agent = f"-javaagent:{self.jacoco_home}/lib/jacocoagent.jar=destfile={self.exec_file},append=true"
        env = os.environ.copy()
        env["JAVA_TOOL_OPTIONS"] = jacoco_agent
        
        simple_class_name = test_class.split(".")[-1]
        
        gson_module_path = os.path.join(project_root, "gson")
        if os.path.exists(gson_module_path):
            cmd = ["mvn", "test", "-pl", "gson", "-Dtest", simple_class_name, "-q"]
        else:
            cmd = ["mvn", "test", "-Dtest", simple_class_name, "-q"]
        
        results = []
        try:
            result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True, timeout=120, env=env)
            output = result.stdout + result.stderr
            
            # 直接输出原始结果，不做正则解析
            if "Tests run:" in output:
                # 提取包含 Tests run 的行
                for line in output.split('\n'):
                    if "Tests run:" in line:
                        # 清理 ANSI 颜色代码输出
                        clean_line = line.replace('\x1b', '').replace('[0m', '').replace('[1m', '')
                        for code in ['[31m', '[32m', '[33m', '[34m', '[0;1m', '[1;31m', '[1;32m', '[1;33m', '[1;34m']:
                            clean_line = clean_line.replace(code, '')
                        print(f"  {clean_line.strip()}")
            else:
                print(f"  ! 无测试输出")
            
            if "JAVA_TOOL_OPTIONS" in os.environ:
                del os.environ["JAVA_TOOL_OPTIONS"]
            
        except subprocess.TimeoutExpired:
            print(f"  ✗ 测试运行超时")
        except Exception as e:
            print(f"  ✗ 测试运行异常: {e}")
        
        return results
    
    def _measure_coverage(self, target_class: str) -> Optional[CoverageReport]:
        """测量代码覆盖率"""
        try:
            # 确定正确的项目路径
            project_root = self.project_dir if os.path.exists(os.path.join(self.project_dir, "pom.xml")) else os.path.dirname(self.project_dir)
            
            # 检查是否是多模块项目，确定正确的classfiles和sourcefiles路径
            gson_module_path = os.path.join(project_root, "gson")
            if os.path.exists(gson_module_path):
                # 多模块项目
                if os.path.exists(os.path.join(self.project_dir, "target/classes")):
                    classfiles_path = os.path.join(self.project_dir, "target/classes")
                    sourcefiles_path = os.path.join(self.project_dir, "src/main/java")
                else:
                    classfiles_path = os.path.join(gson_module_path, "target/classes")
                    sourcefiles_path = os.path.join(gson_module_path, "src/main/java")
            else:
                # 单模块项目
                classfiles_path = os.path.join(self.project_dir, "target/classes")
                sourcefiles_path = os.path.join(self.project_dir, "src/main/java")
            
            # 生成覆盖率报告
            cmd = [
                "java", "-jar",
                f"{self.jacoco_home}/lib/jacococli.jar",
                "report",
                self.exec_file,
                "--classfiles", classfiles_path,
                "--sourcefiles", sourcefiles_path,
                "--xml", "/tmp/jacoco-report.xml"
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode != 0:
                print(f"  ✗ 覆盖率报告生成失败")
                if result.stderr:
                    print(f"    错误信息: {result.stderr[:200]}")
                return None
            
            # 解析覆盖率报告
            coverage = self._parse_coverage_report(target_class)
            return coverage
            
        except Exception as e:
            print(f"  ✗ 覆盖率测量异常: {e}")
            return None
    
    def _parse_coverage_report(self, target_class: str) -> Optional[CoverageReport]:
        """解析JaCoCo XML报告"""
        try:
            import xml.etree.ElementTree as ET
            
            tree = ET.parse("/tmp/jacoco-report.xml")
            root = tree.getroot()
            
            # 查找目标类
            class_name = target_class.split(".")[-1]
            for package in root.findall("package"):
                for cls in package.findall("class"):
                    if cls.get("name", "").endswith(class_name):
                        # 提取覆盖率数据
                        counter = {c.get("type"): c for c in cls.findall("counter")}
                        
                        line_cov = self._calc_coverage(counter.get("LINE"))
                        branch_cov = self._calc_coverage(counter.get("BRANCH"))
                        method_cov = self._calc_coverage(counter.get("METHOD"))
                        
                        covered_lines = int(counter.get("LINE", {}).get("covered", 0)) if "LINE" in counter else 0
                        total_lines = int(counter.get("LINE", {}).get("missed", 0)) + covered_lines if "LINE" in counter else 0
                        
                        report = CoverageReport(
                            class_name=target_class,
                            line_coverage=line_cov,
                            branch_coverage=branch_cov,
                            method_coverage=method_cov,
                            covered_lines=covered_lines,
                            total_lines=total_lines
                        )
                        
                        print(f"  ✓ 行覆盖率: {line_cov:.1f}%, 分支覆盖率: {branch_cov:.1f}%")
                        return report
            
            return None
        except Exception as e:
            print(f"  ✗ 覆盖率解析失败: {e}")
            return None
    
    def _calc_coverage(self, counter) -> float:
        """计算覆盖率百分比"""
        if counter is None:
            return 0.0
        covered = int(counter.get("covered", 0))
        missed = int(counter.get("missed", 0))
        total = covered + missed
        return (covered / total * 100) if total > 0 else 0.0
    
    def get_baseline_coverage(self, target_class: str, baseline_test: str = "JsonReaderTest") -> Optional[CoverageReport]:
        """获取基准覆盖率（不包含新生成的测试）
        
        Args:
            target_class: 目标类全名
            baseline_test: 用于获取基准覆盖率的测试类名
        """
        print(f"[→] 获取基准覆盖率（测试类: {baseline_test}）")
        
        project_root = self.project_dir if os.path.exists(os.path.join(self.project_dir, "pom.xml")) else os.path.dirname(self.project_dir)
        baseline_exec = "/tmp/baseline.exec"
        
        # 清理旧的构建
        gson_module_path = os.path.join(project_root, "gson")
        target_dir = os.path.join(gson_module_path, "target") if os.path.exists(gson_module_path) else os.path.join(self.project_dir, "target")
        if os.path.exists(target_dir):
            subprocess.run(["rm", "-rf", target_dir], capture_output=True)
        
        # 设置JaCoCo代理
        jacoco_agent = f"-javaagent:{self.jacoco_home}/lib/jacocoagent.jar=destfile={baseline_exec}"
        env = os.environ.copy()
        env["JAVA_TOOL_OPTIONS"] = jacoco_agent
        
        # 运行基准测试
        if os.path.exists(gson_module_path):
            cmd = ["mvn", "clean", "test", "-pl", "gson", "-Dtest", baseline_test, "-q"]
        else:
            cmd = ["mvn", "clean", "test", "-Dtest", baseline_test, "-q"]
        
        try:
            result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True, timeout=180, env=env)
            
            if not os.path.exists(baseline_exec):
                print(f"  ✗ 基准覆盖率文件未生成")
                return None
            
            # 从exec文件读取覆盖率
            coverage = self._get_coverage_from_exec(baseline_exec, target_class)
            if coverage:
                print(f"  ✓ 基准覆盖率: 行 {coverage.line_coverage:.1f}%, 分支 {coverage.branch_coverage:.1f}%")
            return coverage
            
        except Exception as e:
            print(f"  ✗ 获取基准覆盖率失败: {e}")
            return None
    
    def _get_coverage_from_exec(self, exec_file: str, target_class: str) -> Optional[CoverageReport]:
        """从JaCoCo exec文件获取覆盖率"""
        try:
            project_root = self.project_dir if os.path.exists(os.path.join(self.project_dir, "pom.xml")) else os.path.dirname(self.project_dir)
            gson_module_path = os.path.join(project_root, "gson")
            
            if os.path.exists(gson_module_path):
                classfiles_path = os.path.join(gson_module_path, "target/classes")
            else:
                classfiles_path = os.path.join(self.project_dir, "target/classes")
            
            # 生成CSV报告（更简单解析）
            csv_file = "/tmp/coverage.csv"
            cmd = [
                "java", "-jar",
                f"{self.jacoco_home}/lib/jacococli.jar",
                "report", exec_file,
                "--classfiles", classfiles_path,
                "--csv", csv_file
            ]
            
            subprocess.run(cmd, capture_output=True, timeout=60)
            
            # 解析CSV
            if not os.path.exists(csv_file):
                return None
            
            class_name = target_class.split(".")[-1]
            with open(csv_file, 'r') as f:
                lines = f.readlines()
                for line in lines[1:]:  # 跳过表头
                    parts = line.strip().split(',')
                    if len(parts) >= 10 and parts[2] == class_name:
                        # CSV格式: GROUP,PACKAGE,CLASS,INSTRUCTION_MISSED,INSTRUCTION_COVERED,BRANCH_MISSED,BRANCH_COVERED,LINE_MISSED,LINE_COVERED,...
                        line_missed = int(parts[7])
                        line_covered = int(parts[8])
                        branch_missed = int(parts[5])
                        branch_covered = int(parts[6])
                        
                        total_lines = line_missed + line_covered
                        total_branches = branch_missed + branch_covered
                        
                        return CoverageReport(
                            class_name=target_class,
                            line_coverage=(line_covered / total_lines * 100) if total_lines > 0 else 0,
                            branch_coverage=(branch_covered / total_branches * 100) if total_branches > 0 else 0,
                            method_coverage=0,
                            covered_lines=line_covered,
                            total_lines=total_lines
                        )
            
            return None
            
        except Exception as e:
            print(f"  ✗ 覆盖率解析失败: {e}")
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


def print_report(report: EvaluationReport):
    """打印评估报告"""
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
        print(f"\n覆盖率报告:")
        print(f"  行覆盖率: {report.coverage.line_coverage:.1f}% ({report.coverage.covered_lines}/{report.coverage.total_lines})")
        print(f"  分支覆盖率: {report.coverage.branch_coverage:.1f}%")
        print(f"  方法覆盖率: {report.coverage.method_coverage:.1f}%")
    
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
