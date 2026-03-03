"""
轻量级LLM交互模块 - 基于litellm的核心LLM功能封装
"""

from typing import List, Dict, Optional
import os
from litellm import acompletion, embedding
from core.color import Colors


class Config:
    """配置管理器 - 负责环境变量和API配置"""
    
    @staticmethod
    def load_env():
        """加载环境变量"""
        try:
            from dotenv import load_dotenv
            load_dotenv()
            return True
        except ImportError:
            return False
    
    @staticmethod
    def get(key: str, default: str = None) -> Optional[str]:
        """获取环境变量值"""
        return os.getenv(key, default)
    
    @staticmethod
    def setup() -> Dict:
        """设置并返回配置字典"""
        dotenv_loaded = Config.load_env()
        
        config = {
            "model": Config.get("CHAT_MODEL", "gpt-3.5-turbo"),
            "embedding_model": Config.get("EMBEDDING_MODEL", "text-embedding-ada-002"),
            "api_key": Config.get("API_KEY"),
            "base_url": Config.get("BASE_URL"),
            "embedding_api_key": Config.get("EMBEDDING_API_KEY"),
            "embedding_base_url": Config.get("EMBEDDING_BASE_URL"),
            "dotenv_loaded": dotenv_loaded
        }
        
        return config


class LLM:
    """语言模型处理器 - 负责聊天完成功能"""
    
    def __init__(self, config: Dict):
        self.model = config["model"]
        self.api_key = config["api_key"]
        self.base_url = config["base_url"]
    
    async def complete(self, messages: List[Dict], model: str = None, **kwargs) -> Dict:
        """执行聊天完成"""
        model = model or self.model
        
        params = {
            "model": model,
            "messages": messages,
            "api_key": self.api_key,
            **kwargs
        }
        
        if self.base_url:
            params["base_url"] = self.base_url
        
        try:
            return await acompletion(**params)
        except Exception as e:
            return {"error": str(e)}


class Embedding:
    """嵌入处理器 - 负责文本嵌入功能"""

    def __init__(self, config: Dict):
        self.model = config["embedding_model"]
        self.api_key = config["embedding_api_key"] or config["api_key"]
        self.base_url = config["embedding_base_url"] or config["base_url"]

    def get(self, texts: List[str], model: str = None) -> Dict:
        """获取文本嵌入向量"""
        model = model or self.model

        params = {
            "model": model,
            "input": texts,
            "api_key": self.api_key
        }

        if self.base_url:
            params["api_base"] = self.base_url

        try:
            return embedding(**params)  # 同步调用
        except Exception as e:
            return {"error": str(e)}
    
    def embed_text(self, text: str, model: str = None) -> Dict:
        return self.get([text], model)

    def embed_texts(self, texts: List[str], model: str = None) -> Dict:
        return self.get(texts, model)
    


class MessageFormatter:
    """消息格式化器 - 负责构建符合API格式的消息"""
    
    @staticmethod
    def build(prompt: str, system: str = None, history: List[Dict] = None) -> List[Dict]:
        """构建消息格式"""
        messages = []
        
        if system:
            messages.append({"role": "system", "content": system})
        
        if history:
            messages.extend(history)
        
        messages.append({"role": "user", "content": prompt})
        return messages


class Client:
    """LLM客户端 - 统一的对外接口"""
    
    def __init__(self, config: Dict = None):
        self.config = config or Config.setup()
        self._check_config()
        
        # 初始化功能组件
        self.llm = LLM(self.config)
        self.embedding = Embedding(self.config)
        self.formatter = MessageFormatter()
    
    def _check_config(self):
        """检查配置完整性"""
        if not self.config["api_key"]:
            print(Colors.yellow("[!]") + f" 警告: 未设置OPENAI_API_KEY")

        if self.config["dotenv_loaded"]:
            print(Colors.green("[√]") + " 环境变量加载成功")

        print("► LLM客户端初始化完成:")
        print(f"  - 模型: {self.config['model']}")
        print(f"  - 嵌入模型: {self.config['embedding_model']}")
        api_status = Colors.green("[√]") if self.config['api_key'] else Colors.red("[×]")
        print(f"  - API状态: {api_status}")
    
    async def chat(self, prompt: str, system: str = None, history: List[Dict] = None, model: str = None, **kwargs) -> str:
        """聊天对话"""
        messages = self.formatter.build(prompt, system, history)
        response = await self.llm.complete(messages, model, **kwargs)

        if "error" in response:
            return f"错误: {response['error']}"

        # 直接返回原始响应
        return response

    def embed(self, texts: List[str], model: str = None) -> Dict:
        """获取文本嵌入"""
        response = self.embedding.get(texts, model)

        if "error" in response:
            raise Exception(f"嵌入获取失败: {response['error']}")

        # 直接返回原始响应
        return response

    def embed_single(self, text: str, model: str = None) -> Dict:
        """获取单个文本的嵌入"""
        return self.embed([text], model)


# 创建全局客户端实例
client = Client()

# 便捷函数 - 保持向后兼容
async def chat(prompt: str, system_prompt: str = None, model: str = None) -> str:
    """简单聊天函数 - 返回文本内容"""
    response = await client.chat(prompt, system_prompt, model=model)

    # 提取文本内容，保持向后兼容
    if "error" in response:
        return response
    try:
        return response.choices[0].message.content
    except (AttributeError, IndexError):
        return "无法获取响应"


def get_embedding(text: str, model: str = None) -> List[float]:
    """获取单个文本的嵌入向量 - 返回嵌入向量"""
    response = client.embed_single(text, model)

    # 提取嵌入向量，保持向后兼容
    try:
        return response["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError):
        raise Exception("无法解析嵌入向量")


if __name__ == "__main__":
    async def demo():
        """功能演示"""
        # 聊天示例
        response = await chat("你好，介绍一下你自己", "你是一个有帮助的AI助手")
        print(f"[💬] 聊天回复: {response}")

        # 嵌入示例
        try:
            emb = get_embedding("这是一个测试文本")
            print(f"[#] 嵌入维度: {len(emb) if emb else 0}")
        except Exception as e:
            print(Colors.red("[×]") + f" 嵌入错误: {e}")

    import asyncio
    asyncio.run(demo())