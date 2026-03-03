# LLM Based Unit Test Generation

UNITTEST
|
|--- rag: Knowledge Collection Module
|--- llm: Unit Test Generation Module
|--- evaluation: Quality evaluation Module
|--- data: Dataset Module
|--- core: Core Module


1. 离线嵌入实现，这一步做好分块、向量模型选取、向量数据库选型；
2. 在线RAG实现，这一步做好检索策略和重排策略；
3. Prompt Builder实现，这个好做，jinja渲染就行；
4. LLM Generator，这个好做，Prompt Template设计好了，只需要抽取一下生成的测试代码就好；
5. 执行和验证，这一块需要做好jacoco集成，判断测试覆盖率，同时可以引入一些其他大模型来评估生成测试的质量，然后生成一些Critic再传给RAG模块去进行检索相关的内容（这里可以做Agentic RAG，让模型决策RAG方案），然后把Critic和相关的内容再传给模型让模型去优化代码质量。
6. 当验证成功，即覆盖率等等都达到要求了，测试代码也可正常执行了，就可以交付结果Result。