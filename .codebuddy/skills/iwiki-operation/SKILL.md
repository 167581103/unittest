---
name: iwiki-operation
description: iwiki创建/修改文档，当需要创建或修改iWiki文档时使用此Skill。然后使用 iwiki MCP 工具直接进行文档的创建、更新等操作。
---

# iWiki 文档创建与修改 Skill

## 概述

此 Skill 用于在 CodeBuddy 中直接创建或修改 iWiki 文档。工作流程分为两步：
1. **查询元数据**：用户给出 page_id,space_id等信息（page_id与doc
_id相同）
2. **执行操作**：使用 iwiki MCP 工具直接对文档进行创建或更新

*当找不到iwiki mcp时，尝试查找iwiki-local mcp*

---

## iwiki MCP 工具参考

### 读取类工具

#### `getDocument` — 获取文档内容
返回文档的完整 Markdown 格式内容。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `docid` | string | ✅ | 文档 ID |

---

### 写入类工具

#### `createDocument` — 创建新文档
在指定空间和父级文档下创建新文档。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `spaceid` | number | ✅ | 空间 ID |
| `parentid` | number | ✅ | 父级文档 ID |
| `title` | string | ✅ | 文档标题 |
| `body` | string | | 文档内容（Markdown 或 HTML） |
| `contenttype` | string | | 文档类型：`MD`（默认）/`DOC`/`FOLDER`/`VIKA` |
| `is_html` | boolean | | 是否按 HTML 解析 body，默认 `false` |

#### `saveDocument` — 更新文档（全量替换）
修改已有文档的标题和/或内容。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `docid` | number | ✅ | 文档 ID |
| `title` | string | ✅ | 文档标题 |
| `body` | string | | 新的文档内容；不传则保持原内容 |
| `is_html` | boolean | | 是否按 HTML 解析 body，默认 `false` |

#### `saveDocumentParts` — 局部更新文档
在文档开头插入或结尾追加内容，不影响现有内容。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | number | ✅ | 文档 ID |
| `title` | string | ✅ | 文档标题 |
| `before` | string | | 插入到文档开头的内容 |
| `after` | string | | 追加到文档结尾的内容 |

---
## 操作指南

### 场景一：修改已有的 iWiki 文档

当用户说"修改 xxx 文件"或"更新 xxx 文档的内容"时：

1. **获取当前内容**（如果需要基于现有内容修改）：
   - 如果有本地副本，直接读取本地文件（如果有本地副本）
   - 如果没有则使用 iwiki MCP 的 `getDocument` 工具，传入 `docid`（值为 pageId 的字符串）

2. **更新文档**：
   - 使用 iwiki MCP 的 `saveDocument` 工具
   - 传入 `docid`（number 类型）、`title`（必填）、`body`（新内容）

### 场景二：创建新的 iWiki 文档

当用户说"创建一个新的 iWiki 文档"时：

1. 获取父目录
   - 用户给出来 doc_id，space_id 则直接使用
   - 如果没有，直接在用户根目录下创建
2. **创建文档**：
   - 使用 iwiki MCP 的 `createDocument` 工具
   - 传入 `spaceid`、`parentid`、`title`
   - mcp返回了 “arguments must be a valid JSON string”，先写到本地临时文件，然后读取写入iwiki文档,规避json转译问题
   - 可选传入 `body`（Markdown 内容）和 `contenttype`（默认 `MD`）

### 场景三：在已有文档末尾追加内容

当用户希望在文档末尾或者头部追加内容而不影响现有内容时：

1. **追加内容**：
   - 使用 iwiki MCP 的 `saveDocumentParts` 工具
   - 传入 `id`（doc_id）、`title`（文档标题）、`after`（要追加的内容）/`before`（要插入的内容）

---

