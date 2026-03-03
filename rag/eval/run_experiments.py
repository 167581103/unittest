"""
实验运行器 - 基于配置文件运行多组RAG评估实验
"""

import os
import sys
import json
import yaml
import importlib
import re
from typing import Dict, List, Any
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import ContextPrecision, ContextRecall, Faithfulness, AnswerRelevancy, FactualCorrectness
from langchain_litellm import ChatLiteLLM
from langchain_openai import OpenAIEmbeddings
from ragas.llms import LangchainLLMWrapper
import pandas as pd


def load_config(config_path: str) -> Dict:
    """加载实验配置文件"""
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config


def substitute_env_vars(value: str) -> str:
    """替换环境变量"""
    if not isinstance(value, str):
        return value

    # 匹配 ${VAR_NAME} 格式
    pattern = r'\$\{([^}]+)\}'
    matches = re.findall(pattern, value)

    for var_name in matches:
        env_value = os.getenv(var_name)
        if env_value is not None:
            value = value.replace(f'${{{var_name}}}', env_value)

    return value


def resolve_config_values(config: Dict) -> Dict:
    """递归解析配置中的环境变量"""
    if isinstance(config, dict):
        return {key: resolve_config_values(value) for key, value in config.items()}
    elif isinstance(config, list):
        return [resolve_config_values(item) for item in config]
    else:
        return substitute_env_vars(config)


def load_rag_class(module_path: str, class_name: str):
    """动态加载RAG实现类"""
    try:
        module = importlib.import_module(module_path)
        rag_class = getattr(module, class_name)

        # 验证类是否继承自 BaseRAG
        from rag.base_rag import BaseRAG
        if not issubclass(rag_class, BaseRAG):
            raise ValueError(f"RAG class {class_name} must inherit from BaseRAG")

        return rag_class
    except (ImportError, AttributeError) as e:
        raise ValueError(f"Failed to load RAG class {class_name} from {module_path}: {e}")


def init_llm(llm_config: Dict):
    """初始化LLM模型"""
    return ChatLiteLLM(
        model=llm_config['model'],
        api_key=llm_config['api_key'],
        api_base=llm_config.get('api_base', None)
    )


def init_embeddings(embedding_config: Dict):
    """初始化嵌入模型"""
    return OpenAIEmbeddings(
        model=embedding_config['model'],
        api_key=embedding_config['api_key'],
        base_url=embedding_config.get('base_url', None)
    )


def load_test_cases(input_path: str) -> List[Dict]:
    """加载测试用例"""
    with open(input_path, 'r', encoding='utf-8') as f:
        test_cases = json.load(f)
    return test_cases


def generate_ragas_dataset(
    rag_instance,
    test_cases: List[Dict],
    top_k: int = 3
) -> List[Dict]:
    """生成RAGAS评估数据集"""
    ragas_dataset_list = []

    for i, test_case in enumerate(test_cases, 1):
        query = test_case['query']
        reference_methods = test_case['methods_used']

        print(f"  [{i}/{len(test_cases)}] 处理测试: {test_case['test_name']}")
        print(f"    Query: {query}")
        print(f"    Reference methods: {reference_methods}")

        try:
            # 召回相关文档
            relevant_docs = rag_instance.get_most_relevant_docs(query, top_k=top_k)
            print(f"    召回 {len(relevant_docs)} 个文档")

            # 生成答案
            response = rag_instance.generate_answer(query, relevant_docs)
            print(f"    生成答案 (前100字符): {response[:100]}...")

            # 构建RAGAS数据集条目
            dataset_entry = {
                "user_input": query,
                "retrieved_contexts": relevant_docs,
                "response": response,
                "reference": json.dumps(reference_methods, ensure_ascii=False)
            }
            ragas_dataset_list.append(dataset_entry)

        except Exception as e:
            print(f"    [ERROR] 处理失败: {e}")
            continue

    return ragas_dataset_list


def run_evaluation(
    ragas_dataset_list: List[Dict],
    llm,
    embeddings,
    metrics: List[str]
) -> Dict:
    """运行RAGAS评估"""

    # 构建RAGAS数据集
    ragas_dataset = Dataset.from_dict({
        "user_input": [item["user_input"] for item in ragas_dataset_list],
        "retrieved_contexts": [item["retrieved_contexts"] for item in ragas_dataset_list],
        "response": [item["response"] for item in ragas_dataset_list],
        "reference": [item["reference"] for item in ragas_dataset_list]
    })

    # 映射指标名称到指标对象
    metric_map = {
        "context_precision": ContextPrecision(),
        "context_recall": ContextRecall(),
        "faithfulness": Faithfulness(),
        "answer_relevancy": AnswerRelevancy(),
        "factual_correctness": FactualCorrectness()
    }

    selected_metrics = [metric_map[m] for m in metrics if m in metric_map]

    # 创建评估器
    evaluator_llm = LangchainLLMWrapper(llm)

    # 执行评估
    print(f"  运行RAGAS评估，指标: {[m.name for m in selected_metrics]}")
    result = evaluate(
        dataset=ragas_dataset,
        metrics=selected_metrics,
        llm=evaluator_llm,
        embeddings=embeddings
    )

    return result


def save_results(
    result,
    output_path: str,
    detailed_path: str,
    num_samples: int,
    metrics: List[str]
):
    """保存评估结果"""
    df = result.to_pandas()

    # 保存JSON结果
    evaluation_results = {
        "num_samples": num_samples,
        "metrics": metrics,
        "scores": {}
    }

    # 计算平均分
    for metric_name in metrics:
        if metric_name in df.columns:
            evaluation_results["scores"][metric_name] = float(df[metric_name].mean())

    evaluation_results["details"] = df.to_dict(orient='records')

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(evaluation_results, f, ensure_ascii=False, indent=2)

    print(f"  评估结果已保存到: {output_path}")

    # 保存详细CSV
    df.to_csv(detailed_path, index=False, encoding='utf-8')
    print(f"  详细结果已保存到: {detailed_path}")

    return evaluation_results


def run_single_experiment(experiment_config: Dict) -> Dict:
    """运行单个实验"""
    exp_name = experiment_config['name']
    description = experiment_config['description']

    print("\n" + "=" * 80)
    print(f"运行实验: {exp_name}")
    print(f"描述: {description}")
    print("=" * 80)

    # 解析环境变量
    config = resolve_config_values(experiment_config)

    # 1. 加载测试用例
    print("\n[步骤 1] 加载测试用例")
    test_cases = load_test_cases(config['evaluation']['step1_input'])
    print(f"  加载了 {len(test_cases)} 条测试用例")

    # 2. 加载RAG实现类
    print("\n[步骤 2] 加载RAG实现")
    rag_config = config['rag_implementation']
    rag_class = load_rag_class(rag_config['module'], rag_config['class'])

    # 3. 初始化LLM和嵌入模型
    print("\n[步骤 3] 初始化LLM和嵌入模型")
    llm = init_llm(config['llm'])
    embeddings = init_embeddings(config['embedding'])
    print(f"  LLM模型: {config['llm']['model']}")
    print(f"  嵌入模型: {config['embedding']['model']}")

    # 4. 初始化RAG实例
    print("\n[步骤 4] 初始化RAG实例")
    rag = rag_class(llm=llm, embeddings=embeddings, index_path=rag_config['index_path'])

    # 5. 检查是否已生成RAGAS数据集
    eval_config = config['evaluation']
    step2_output = eval_config['step2_output']

    if os.path.exists(step2_output):
        print(f"\n  RAGAS数据集已存在: {step2_output}")
        print(f"  跳过数据集生成，直接加载...")

        with open(step2_output, 'r', encoding='utf-8') as f:
            ragas_dataset_list = json.load(f)
    else:
        # 生成RAGAS数据集
        print(f"\n[步骤 5] 生成RAGAS数据集")
        top_k = eval_config['retrieval'].get('top_k', 3)
        ragas_dataset_list = generate_ragas_dataset(rag, test_cases, top_k=top_k)

        print(f"\n  成功生成 {len(ragas_dataset_list)} 条RAGAS数据集记录")

        # 保存RAGAS数据集
        with open(step2_output, 'w', encoding='utf-8') as f:
            json.dump(ragas_dataset_list, f, ensure_ascii=False, indent=2)
        print(f"  RAGAS数据集已保存到: {step2_output}")

    # 6. 运行评估
    print(f"\n[步骤 6] 运行RAGAS评估")
    result = run_evaluation(
        ragas_dataset_list,
        llm,
        embeddings,
        eval_config['metrics']
    )

    # 7. 保存结果
    print(f"\n[步骤 7] 保存评估结果")
    evaluation_results = save_results(
        result,
        eval_config['evaluation_output'],
        eval_config['detailed_output'],
        len(ragas_dataset_list),
        eval_config['metrics']
    )

    print(f"\n实验 {exp_name} 完成！")
    print("\n指标得分:")
    for metric, score in evaluation_results['scores'].items():
        print(f"  {metric}: {score:.4f}")

    return evaluation_results


def generate_summary_report(all_results: List[Dict], summary_config: Dict):
    """生成汇总报告"""
    print("\n" + "=" * 80)
    print("生成汇总报告")
    print("=" * 80)

    # 构建汇总数据
    summary_data = []
    for result in all_results:
        exp_name = result['experiment_name']
        scores = result['scores']

        row = {'experiment': exp_name, **scores}
        summary_data.append(row)

    # 创建DataFrame
    df = pd.DataFrame(summary_data)

    # 保存为JSON
    summary_json = {
        'num_experiments': len(all_results),
        'experiments': all_results,
        'comparison': df.to_dict(orient='records')
    }

    with open(summary_config['output_file'], 'w', encoding='utf-8') as f:
        json.dump(summary_json, f, ensure_ascii=False, indent=2)

    print(f"\n汇总报告已保存到: {summary_config['output_file']}")

    # 保存为CSV
    df.to_csv(summary_config['csv_output'], index=False, encoding='utf-8')
    print(f"汇总CSV已保存到: {summary_config['csv_output']}")

    # 打印汇总表格
    print("\n实验对比:")
    print(df.to_string(index=False))

    return summary_json


def main(config_path: str = None):
    """主函数"""
    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "exp.yaml")

    # 加载配置
    print("加载实验配置...")
    config = load_config(config_path)

    # 运行实验
    all_results = []

    for exp_config in config['experiments']:
        # 检查是否启用
        if not exp_config.get('enabled', True):
            print(f"\n跳过实验: {exp_config['name']} (未启用)")
            continue

        try:
            results = run_single_experiment(exp_config)
            results['experiment_name'] = exp_config['name']
            results['description'] = exp_config['description']
            all_results.append(results)
        except Exception as e:
            print(f"\n实验 {exp_config['name']} 失败: {e}")
            import traceback
            traceback.print_exc()
            continue

    # 生成汇总报告
    if all_results:
        summary_config = config.get('summary', {})
        if summary_config:
            generate_summary_report(all_results, summary_config)
    else:
        print("\n没有成功运行的实验！")

    print("\n" + "=" * 80)
    print("所有实验完成！")
    print("=" * 80)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='运行RAG评估实验')
    parser.add_argument('--config', '-c', type=str,
                       help='实验配置文件路径')
    parser.add_argument('--exp', '-e', type=str,
                       help='只运行指定的实验（按名称）')

    args = parser.parse_args()

    if args.exp:
        # 只运行单个实验
        config_path = args.config or os.path.join(os.path.dirname(__file__), "exp.yaml")
        config = load_config(config_path)

        # 解析环境变量
        config = resolve_config_values(config)

        # 找到指定的实验
        target_exp = None
        for exp_config in config['experiments']:
            if exp_config['name'] == args.exp:
                target_exp = exp_config
                break

        if target_exp:
            if target_exp.get('enabled', True):
                results = run_single_experiment(target_exp)
            else:
                print(f"实验 {args.exp} 未启用")
        else:
            print(f"未找到实验: {args.exp}")
    else:
        # 运行所有实验
        main(args.config)
