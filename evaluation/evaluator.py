"""
极简Evaluation模块 - 测试执行与覆盖率评估

功能：
1. 执行生成的单元测试
2. 收集测试结果
3. 评估代码覆盖率
4. 生成评估报告
"""

import os
import re
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
    compilation_success: bool
    errors: List[str]


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
        target_method: str
    ) -> EvaluationReport:
        """
        评估生成的测试
        
        Args:
            test_file: 测试文件路径
            test_class: 测试类全名（如 com.google.gson.stream.JsonReaderTest）
            target_class: 被测试的目标类
            target_method: 被测试的目标方法
            
        Returns:
            评估报告
        """
        errors = []
        
        # 1. 复制测试文件到项目
        print(f"[→] 复制测试文件: {test_file}")
        copy_success = self._copy_test_file(test_file, test_class)
        if not copy_success:
            errors.append("复制测试文件失败")
            return EvaluationReport(
                test_file=test_file,
                target_class=target_class,
                target_method=target_method,
                test_results=[],
                coverage=None,
                compilation_success=False,
                errors=errors
            )
        
        # 使用实际的测试类名（可能添加了Generated后缀）
        actual_test_class = self._actual_test_class or test_class
        
        # 2. 编译测试
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
                compilation_success=False,
                errors=errors
            )
        
        # 3. 运行测试
        print(f"[→] 运行测试: {actual_test_class}")
        test_results = self._run_test(actual_test_class)
        
        # 4. 评估覆盖率
        print(f"[→] 评估覆盖率: {target_class}")
        coverage = self._measure_coverage(target_class)
        
        return EvaluationReport(
            test_file=test_file,
            target_class=target_class,
            target_method=target_method,
            test_results=test_results,
            coverage=coverage,
            compilation_success=True,
            errors=errors
        )
    
    def _copy_test_file(self, test_file: str, test_class: str) -> bool:
        """复制测试文件到项目测试目录"""
        try:
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
            
            # 构建目标路径
            if package:
                package_path = package.replace(".", "/")
                target_dir = Path(self.project_dir) / "src/test/java" / package_path
            else:
                target_dir = Path(self.project_dir) / "src/test/java"
            target_dir.mkdir(parents=True, exist_ok=True)
            
            # 读取并修改内容
            target_file = target_dir / f"{unique_class_name}.java"
            with open(test_file, 'r', encoding='utf-8') as src:
                content = src.read()
            
            # 替换类名
            content = content.replace(f"public class {original_class_name}", 
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
    
    def _compile_test(self, test_class: str) -> bool:
        """编译测试文件"""
        cmd = [
            "mvn", "test-compile",
            "-pl", "gson",
            "-am",
            "-q"  # 静默模式
        ]
        
        try:
            result = subprocess.run(
                cmd,
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                timeout=120
            )
            success = result.returncode == 0
            if not success:
                print(f"  ✗ 编译错误:\n{result.stderr[:500]}")
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
        # 设置JaCoCo代理
        jacoco_agent = f"-javaagent:{self.jacoco_home}/lib/jacocoagent.jar=destfile={self.exec_file},append=true"
        env = os.environ.copy()
        env["JAVA_TOOL_OPTIONS"] = jacoco_agent
        
        cmd = [
            "mvn", "test",
            "-pl", "gson",
            "-Dtest", test_class,
            "-q"
        ]
        
        results = []
        try:
            result = subprocess.run(
                cmd,
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                timeout=120,
                env=env
            )
            
            # 解析测试结果
            results = self._parse_test_output(result.stdout + result.stderr)
            
            # 清理JaCoCo环境变量
            if "JAVA_TOOL_OPTIONS" in os.environ:
                del os.environ["JAVA_TOOL_OPTIONS"]
            
        except subprocess.TimeoutExpired:
            print(f"  ✗ 测试运行超时")
        except Exception as e:
            print(f"  ✗ 测试运行异常: {e}")
        
        return results
    
    def _parse_test_output(self, output: str) -> List[TestResult]:
        """解析测试输出"""
        results = []
        
        # 解析测试运行结果（简化版）
        # Maven输出格式：Tests run: X, Failures: Y, Errors: Z, Skipped: W
        test_run_pattern = r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+)"
        match = re.search(test_run_pattern, output)
        
        if match:
            total = int(match.group(1))
            failures = int(match.group(2))
            errors = int(match.group(3))
            passed = total - failures - errors
            
            print(f"  ✓ 测试运行: {total}个, 通过: {passed}个, 失败: {failures}个, 错误: {errors}个")
        else:
            print(f"  ! 无法解析测试结果")
        
        return results
    
    def _measure_coverage(self, target_class: str) -> Optional[CoverageReport]:
        """测量代码覆盖率"""
        try:
            # 生成覆盖率报告
            cmd = [
                "java", "-jar",
                f"{self.jacoco_home}/lib/jacococli.jar",
                "report",
                self.exec_file,
                "--classfiles", f"{self.project_dir}/gson/target/classes",
                "--sourcefiles", f"{self.project_dir}/gson/src/main/java",
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
