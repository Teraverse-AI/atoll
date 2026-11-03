# native_tools（原生工具）

**简体中文** | [English](./README.en.md)

本目录是 Atoll 对 Archipelago `mcp_servers/` 的替代，也是整个仓库布局中唯一的结构性改动。目录与 Archipelago 的 **11 个服务器模块一一对应**（另加 Atoll 自有的 `core/` 核心组件）：每个模块一个子文件夹，内含可导入的服务器包、`tests/` 测试目录和 README。已实现的模块**逐字移植**了上游服务器对模型可见的全部表面（工具名、参数 schema、描述文本、结果与错误格式）——LLM 无法区分 Atoll 的原生函数与 Archipelago 的 MCP 服务器。

模型观察到的工具名遵循 FastMCP 网关规则，与 Archipelago 完全一致：只挂载一个服务器时为 `{tool_name}`；挂载多个时为 `{server_name}_{tool_name}`（如 `sheets_server_read_tab`）。可用 `load_tools(["filesystem_server", "sheets_server"])` 挂载子集，或在 `task.toml` 中按任务配置 `servers = [...]`；`load_default_tools()` 挂载全部已实现的服务器。

## 模块索引

✅ = 已完整移植（含测试） · 📄 = 仅 README 占位（尚未移植）

### ✅ `filesystem/` → 包 `filesystem_server`

| 工具 | 说明 |
| --- | --- |
| `list_files` | 列出路径下的文件和文件夹，每项显示名称与类型。 |
| `read_text_file` | 读取文本文件内容；`.eml` 邮件会解码返回（头部、正文、附件清单）。 |
| `read_image_file` | 读取图片文件并以视觉 API 可用的形式返回。 |
| `search_files` | 在指定目录按 glob 模式搜索文件。 |
| `get_file_metadata` | 返回文件/目录的元数据（路径、类型、大小、MIME、权限、修改时间）。 |
| `get_directory_tree` | 以 ASCII 树形式展示目录结构。 |

### ✅ `spreadsheets/` → 包 `sheets_server`

| 工具 | 说明 |
| --- | --- |
| `create_spreadsheet` | 创建新的 `.xlsx` 工作簿（经校验的表定义、表头与数据行）。 |
| `delete_spreadsheet` | 删除 Excel 工作簿文件。 |
| `read_tab` | 读取某个工作表或区域，支持公式提取与截断提示。 |
| `read_csv` | 读取 CSV（分隔符/编码可配置），返回结构化行。 |
| `list_tabs_in_spreadsheet` | 列出工作簿中所有工作表及元数据。 |
| `add_tab` | 向现有工作簿添加新工作表，可带初始数据。 |
| `delete_tab` | 删除某个工作表。 |
| `edit_spreadsheet` | 原子化批量编辑（16 种操作：单元格、行列、格式、条件格式、数据验证、命名区域、图片等）。 |
| `add_content_text` | 向指定单元格写入文本/数字/公式。 |
| `delete_content_cell` | 清空指定单元格内容。 |
| `create_chart` | 基于数据区域创建图表（柱状/折线/饼图）。 |
| `filter_tab` | 按列条件过滤行，并可持久化自动筛选条件。 |

### ✅ `pdfs/` → 包 `pdf_server`

| 工具 | 说明 |
| --- | --- |
| `read_pdf_pages` | 读取 PDF 页面：文本、图片标注键（`page{N}_img{M}`）、删除线标注、表单字段；支持密码。 |
| `search_pdf` | 跨页全文搜索。 |
| `create_pdf` | 由结构化内容块生成 PDF 文档。 |
| `read_image` | 按标注键提取 PDF 内嵌图片。 |
| `read_page_as_image` | 将单页渲染为 JPEG 图片（144 DPI）。 |

### ✅ `code/` → 包 `code_execution_server`

| 工具 | 说明 |
| --- | --- |
| `python_code_execution` | 运行 Python 3 代码，返回 stdout、stderr 与退出码；描述中列出沙箱预装包（与 Archipelago 相同的配置）。 |
| `code_exec` | 在沙箱中执行 shell 命令，并保留上游的输出启发式（二进制/HTML 抑制、首尾截断）。 |

两个工具**始终**在无网络、限内存的 Docker 容器中执行（镜像 `atoll-sandbox:latest`，由 `scripts/build_sandbox_image.sh` 构建）——绝不在宿主机上运行。

### ✅ `documents/` → 包 `docs_server`

| 工具 | 说明 |
| --- | --- |
| `create_document` | 由结构化内容块（段落、标题、列表、表格、分页/分节符）创建新 `.docx` 文档，支持元数据。 |
| `delete_document` | 从文件系统删除 `.docx` 文档。 |
| `get_document_overview` | 文档结构概览：标题层级、段落数、分页信息。 |
| `read_document_content` | 读取结构化内容并返回稳定编辑标识符（`body.p.0`、`body.tbl.0.r.0.c.0`）；按内容量分页；呈现修订（tracked changes）与内嵌图片。 |
| `read_image` | 按标注键提取文档内嵌图片（由 read_document_content 缓存）。 |
| `add_content_text` | 在标识符位置插入文本（run/段落/单元格；首部或尾部）。 |
| `edit_content_text` | 替换标识符处的文本内容。 |
| `delete_content_text` | 清空标识符处内容，或整体删除该元素。 |
| `add_image` | 向段落/run 插入图片文件，可指定尺寸。 |
| `modify_image` | 按索引旋转/翻转/调亮度/调对比度内嵌图片。 |
| `apply_formatting` | 对 run、段落或单元格应用加粗/斜体/下划线/删除线/字号/颜色。 |
| `header_footer` | 读取、设置、清空或链接节的页眉/页脚（action 驱动）。 |
| `page_margins` | 读取或设置节的页边距（action 驱动）。 |
| `page_orientation` | 读取或设置纵向/横向（action 驱动）。 |
| `comments` | 读取、添加或删除文档批注（action 驱动；需 python-docx ≥ 1.2）。 |

### 📄 尚未移植（仅 README 占位）

| 模块 | 原服务器功能 | 上游工具（概要） | 财务相关性 |
| --- | --- | --- | --- |
| `edgar_sec/` | SEC EDGAR 披露文件访问与分析 | `lookup_cik`、`get_company_submissions`、`get_company_facts`、`get_company_concept`、`get_frames`、`list_filing_documents`、`get_filing_document`、`get_debt_schedule`、`get_equity_compensation`、`extract_html_table` | **高**（需联网/API） |
| `fmp/` | 行情与基本面数据（Financial Modeling Prep API） | 报价、K 线与技术指标、公司信息/搜索、财务报表、盈利/分红、分析师数据、DCF 估值、SEC 文件、新闻、宏观、指数、ETF/基金、外汇、加密货币、大宗商品、国会交易 | **高**（需付费 API） |
| `mail/` | 模拟邮箱应用 | `list_mails`、`read_mail`、`search_mail`、`send_mail`、`reply_mail`、`reply_all_mail`、`forward_mail` | 中 |
| `calendar/` | 模拟日历应用 | `list_events`、`read_event`、`create_event`、`update_event`、`delete_event` | 低 |
| `chat/` | 模拟团队聊天（频道/线程） | 频道与线程读取、用户、发帖/回复、表情回应 | 低 |
| `presentations/` | 幻灯片应用 | 幻灯片的创建/读取/编辑、形状、图片、表格、图表 | 低 |

每个占位文件夹的 README 介绍了原服务器的功能及暂缓移植的原因。只挂载已移植服务器的任务，其工具表面与在 Archipelago 上配置相同 MCP 服务器的任务完全一致。

## 核心组件（`core/` → 包 `atoll_tools`）

- `registry.py` — `@register("<server>")`；`load_tools([...])` 应用网关命名规则。
- `tool_schema.py` — 已移植工具**逐字输出其 vendored 的已发布 schema**（见 `published.py`）；签名内省（`Annotated[T, Field(description=...)]` 或 Google 风格 `Args:`）仅作为新工具的后备。
- `published.py` — 每个已移植服务器携带一份 `published_schemas.json`：即 Archipelago 网关在上游 pin 定提交处 `tools/list` 实际返回的 JSON（按各服务器锁定的 fastmcp 版本抓取——上游会对 schema 做 `flatten_schema`、`(Optional)`/nullable 标注，documents 还做 union 合并；若干工具以单个嵌套 `input` 对象发布）。同时移植了上游的 EnvelopeCompatMiddleware：对 `input` 封装工具的扁平参数调用会在执行前被包装。
- `context.py` — 每次运行的工作区根目录；所有路径都在其内解析，越界访问被拒绝。
- `content.py` — `ImageContent`，等价于 MCP 的 image 内容块，用于返回图片的工具。

## 新增工具的约定

```python
from typing import Annotated
from pydantic import Field
from atoll_tools.registry import register

@register("docs_server")
def my_tool(
    amount: Annotated[float, Field(description="这个数字的含义。")],
) -> dict:
    """模型可见的一句话摘要。"""
    return {"ok": True}
```

返回 `str` 表示模型将逐字看到的文本（移植工具均如此）；返回可 JSON 序列化的 `dict`/pydantic 模型表示结构化结果；返回 `ImageContent` 表示图片。工具抛出的异常将以 `Error executing tool <name>: <message>`（MCP 网关格式）呈现给模型。
