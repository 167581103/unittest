import os
import re
import json
import argparse
from pathlib import Path
import random
from typing import Optional, List
from dataclasses import dataclass, asdict

# 核心配置
TEST_ANNOTATION = "@Test"
FINAL_SAMPLE_SIZE = 1000
# 方法签名提取正则（简化版，适配Java方法）
METHOD_SIGNATURE_PATTERN = re.compile(
    r'(public|private|protected)\s+[\w<>?\[\]]+\s+(\w+)\s*\([^)]*\)\s*(throws\s+\w+.*?)?\s*\{',
    re.DOTALL | re.MULTILINE
)

@dataclass
class MethodPair:
    """测试方法-被测方法数据对（结构化存储）"""
    project_name: str          # 所属项目名
    test_file_path: str        # 测试文件完整路径
    target_file_path: str      # 被测文件完整路径
    test_method_name: str      # 测试方法名
    target_method_name: str    # 被测方法名
    test_class_context: str    # 测试类上下文（导入+成员变量+其他方法签名）
    test_method_body: str      # 测试方法完整方法体
    target_class_context: str  # 被测类上下文（导入+成员变量+其他方法签名）
    target_method_body: str    # 被测方法完整方法体

def read_file_content(file: Path) -> Optional[str]:
    """读取文件完整内容（容错处理）"""
    try:
        with open(file, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        try:
            with open(file, "r", encoding="gbk") as f:
                return f.read()
        except Exception as e:
            print(f"读取文件失败 {file} (编码错误): {e}")
            return None
    except Exception as e:
        print(f"读取文件失败 {file}: {e}")
        return None

def extract_imports(content: str) -> str:
    """提取文件的import依赖（保留完整import块）"""
    import_lines = []
    lines = content.splitlines()
    for line in lines:
        stripped_line = line.strip()
        if stripped_line.startswith(('import ', 'package ')):
            import_lines.append(line)
        elif stripped_line and not stripped_line.startswith(('//', '/*')):
            break  # 非import行，终止提取
    return '\n'.join(import_lines) + '\n\n'

def extract_method_body(content: str, method_name: str) -> Optional[str]:
    """
    提取指定方法的完整方法体（包含花括号）
    计数器法解析平衡花括号，兼容Python 3.9
    """
    # 定位方法起始位置（找到方法名后的第一个{）
    method_start = re.search(
        rf'\b{method_name}\s*\([^)]*\)\s*(throws\s+\w+.*?)?\s*{{',
        content,
        re.DOTALL | re.IGNORECASE
    )
    if not method_start:
        return None
    
    # 计数器法匹配平衡花括号
    start_idx = method_start.end() - 1  # { 的位置
    end_idx = start_idx
    brace_count = 1
    content_len = len(content)
    
    while brace_count > 0 and end_idx < content_len - 1:
        end_idx += 1
        char = content[end_idx]
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
    
    if brace_count == 0:
        return content[start_idx:end_idx+1]
    else:
        print(f"⚠️  方法 {method_name} 花括号不匹配，跳过")
        return None

def extract_class_context(content: str, exclude_method: str) -> str:
    """
    提取类上下文：保留import + 类结构 + 成员变量 + 其他方法签名（排除目标方法）
    :param content: 文件完整内容
    :param exclude_method: 需要排除的方法名（只保留签名，移除方法体）
    """
    # 1. 提取import依赖
    imports = extract_imports(content)
    
    # 2. 提取类结构（简化版，匹配class到文件结束）
    class_match = re.search(r'(class\s+\w+.*?\{.*)', content, re.DOTALL)
    if not class_match:
        return imports
    
    class_content = class_match.group(1)
    context_lines = []
    lines = class_content.splitlines()
    in_exclude_method = False
    brace_count = 0
    
    for line in lines:
        stripped_line = line.strip()
        
        # 检测是否进入排除方法体
        if exclude_method in stripped_line and '{' in stripped_line and not in_exclude_method:
            # 保留方法签名，标记进入方法体
            sig_part = stripped_line.split('{')[0] + '{ // 方法体已省略'
            context_lines.append(sig_part)
            in_exclude_method = True
            brace_count = 1
            continue
        
        # 处理排除方法体内部
        if in_exclude_method:
            # 统计花括号，判断是否退出方法体
            for char in line:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
            if brace_count == 0:
                in_exclude_method = False
            continue
        
        # 保留非方法体的内容（成员变量、其他方法签名）
        context_lines.append(line)
    
    return imports + '\n'.join(context_lines)

def extract_test_methods(content: str) -> List[str]:
    """从测试文件中提取所有@Test注解的方法名"""
    test_methods = []
    # 匹配@Test注解后的方法
    test_pattern = re.compile(
        rf'{TEST_ANNOTATION}\s*\n*\s*(public|private|protected)\s+[\w<>?\[\]]+\s+(\w+)\s*\([^)]*\)',
        re.DOTALL | re.MULTILINE
    )
    matches = test_pattern.findall(content)
    for _, method_name in matches:
        test_methods.append(method_name)
    return test_methods

def extract_target_methods(content: str, test_method_body: str) -> List[str]:
    """从测试方法体中提取被测方法名（简单启发式匹配）"""
    # 匹配类似 obj.method() 的调用
    call_pattern = re.compile(r'(\w+)\.(\w+)\s*\([^)]*\)', re.DOTALL)
    matches = call_pattern.findall(test_method_body)
    # 去重，过滤常见方法（如equals、toString等）
    exclude_methods = {'equals', 'toString', 'hashCode', 'getClass', 'size', 'isEmpty', 'add', 'remove'}
    target_methods = [m for _, m in matches if m not in exclude_methods and len(m) > 1]
    return list(set(target_methods))  # 去重

def match_target_file(test_file: Path) -> Optional[Path]:
    """测试文件 → 被测文件：路径替换规则"""
    test_path = str(test_file)
    target_path = test_path.replace("src/test/java", "src/main/java").replace("Test.java", ".java")
    target_file = Path(target_path)
    return target_file if target_file.exists() else None

def process_method_pair(
    project_name: str,
    test_file: Path,
    target_file: Path
) -> List[MethodPair]:
    """处理单个文件对，提取所有测试方法-被测方法对"""
    method_pairs = []
    
    # 1. 读取文件内容
    test_content = read_file_content(test_file)
    target_content = read_file_content(target_file)
    if not test_content or not target_content:
        return method_pairs
    
    # 2. 提取测试文件中的所有@Test方法
    test_methods = extract_test_methods(test_content)
    if not test_methods:
        return method_pairs
    
    # 3. 遍历每个测试方法，匹配被测方法
    for test_method in test_methods:
        # 提取测试方法体
        test_body = extract_method_body(test_content, test_method)
        if not test_body:
            continue
        
        # 提取测试类上下文（排除当前测试方法体）
        test_context = extract_class_context(test_content, test_method)
        
        # 从测试方法体中提取被测方法名
        target_methods = extract_target_methods(test_content, test_body)
        if not target_methods:
            continue
        
        # 4. 遍历被测方法，生成数据对
        for target_method in target_methods:
            # 提取被测方法体
            target_body = extract_method_body(target_content, target_method)
            if not target_body:
                continue
            
            # 提取被测类上下文（排除当前被测方法体）
            target_context = extract_class_context(target_content, target_method)
            
            # 构建方法对
            pair = MethodPair(
                project_name=project_name,
                test_file_path=str(test_file.absolute()),
                target_file_path=str(target_file.absolute()),
                test_method_name=test_method,
                target_method_name=target_method,
                test_class_context=test_context,
                test_method_body=test_body,
                target_class_context=target_context,
                target_method_body=target_body
            )
            method_pairs.append(pair)
            print(f"✅ 匹配成功：{project_name} → {test_file.name}#{test_method} → {target_file.name}#{target_method}")
    
    return method_pairs

def process_single_project(project_dir: Path) -> List[MethodPair]:
    """处理单个项目，提取所有测试方法-被测方法对"""
    all_method_pairs = []
    project_name = project_dir.name  # 项目名（目录名）
    
    # 全局找所有测试文件
    test_files = list(project_dir.rglob("src/test/java/**/*.java"))
    if not test_files:
        print(f"⚠️  项目 {project_name} 未找到测试文件，跳过")
        return all_method_pairs
    
    print(f"\n===== 开始处理项目：{project_name} ======")
    print(f"找到 {len(test_files)} 个测试文件")

    for test_file in test_files:
        # 匹配对应的源码文件
        target_file = match_target_file(test_file)
        if not target_file:
            continue
        
        # 提取方法级数据对
        method_pairs = process_method_pair(project_name, test_file, target_file)
        all_method_pairs.extend(method_pairs)
    
    print(f"项目 {project_name} 共匹配 {len(all_method_pairs)} 组方法对")
    return all_method_pairs

def read_project_list(file_path: str) -> List[str]:
    """从project_list.txt读取项目名列表（每行一个）"""
    project_names = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                project_name = line.strip()
                if project_name:  # 跳过空行
                    project_names.append(project_name)
        print(f"从 {file_path} 读取到 {len(project_names)} 个项目名")
    except Exception as e:
        print(f"❌ 读取项目列表失败 {file_path}: {e}")
        exit(1)
    return project_names

def main():
    parser = argparse.ArgumentParser(description="批量提取多项目的测试方法-被测方法数据对（保留类上下文）")
    parser.add_argument("--base_dir", required=True, help="项目基准目录（如./projects，项目路径为 base_dir/项目名）")
    parser.add_argument("--project_list", default="project_list.txt", help="项目名列表文件（每行一个项目名）")
    parser.add_argument("--output", default="benchmark.json", help="输出文件路径")
    parser.add_argument("--sample_size", type=int, default=FINAL_SAMPLE_SIZE, help="最终抽样总数量（所有项目合计）")
    args = parser.parse_args()

    # 1. 读取项目列表
    project_names = read_project_list(args.project_list)
    if not project_names:
        print("❌ 未读取到任何项目名，退出")
        exit(1)
    
    # 2. 批量处理所有项目
    all_project_pairs = []
    base_dir = Path(args.base_dir).absolute()
    
    for project_name in project_names:
        project_dir = base_dir / project_name
        if not project_dir.exists():
            print(f"⚠️  项目目录不存在：{project_dir}，跳过")
            continue
        # 处理单个项目
        project_pairs = process_single_project(project_dir)
        all_project_pairs.extend(project_pairs)
    
    # 3. 抽样（保证不超过实际数量）
    total_pairs = len(all_project_pairs)
    sample_size = min(args.sample_size, total_pairs)
    final_data = random.sample(all_project_pairs, sample_size) if all_project_pairs else []
    
    # 4. 保存结果
    final_data_dict = [asdict(pair) for pair in final_data]
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(final_data_dict, f, ensure_ascii=False, indent=2)
    
    # 输出统计信息
    print(f"\n===== 所有项目处理完成 ======")
    print(f"总计匹配方法对数量：{total_pairs}")
    print(f"抽样后方法对数量：{len(final_data)}")
    print(f"输出文件：{Path(args.output).absolute()}")

if __name__ == "__main__":
    main()