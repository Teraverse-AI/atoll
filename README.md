# Atoll

**简体中文** | [English](./README.en.md)

***A**gentic **T**ask **O**ntology for **LL**M*

<a href="LICENSE"><img src="https://img.shields.io/badge/📜-Apache%202.0-b31b1b"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/🐍-Python%203.12+-yellow"></a>
<a href="https://github.com/Mercor-Intelligence/archipelago"><img src="https://img.shields.io/badge/🏝️-Archipelago%20compatible-0ea5e9"></a>
<a href="mailto:dev@teravs.com"><img src="https://img.shields.io/badge/✉️-Contact-green"></a>

**Atoll** 是由 Teraverse Limited 开发的轻量级系统，用于在专业的、需调用工具的任务上运行和评测 AI 智能体——目前主要面向会计与财务领域。它是 Mercor [Archipelago](https://github.com/Mercor-Intelligence/archipelago) 的可直接对标版本：相同的架构边界、相同的模型可见工具表面，而资源占用仅为其一小部分。Atoll 由三个主要组件构成：

1. **Environment（环境）**：工作区生命周期管理（初始化填充、快照）——一个纯 Python 库，无网关进程
2. **Agents（智能体）**：可扩展的智能体运行器，内含可配置智能体实现的注册表
3. **Grading（评分）**：对智能体新增或修改的文件（运行前后快照之差）逐条评分，与参考答案对照，按权重折算分数——见 [grading/README.md](./grading/README.md)

**设计理念——以复用实现可比性，而非重新实现。** 运行中的模型无法区分 Atoll 与 Archipelago，评测分数可直接比较，因为模型能观察到的一切都来自 Mercor 本身，而非仿制：

- **工具表面**：工具名称、参数 schema、描述文本及结果/错误格式均从 Archipelago 的 MCP 服务器逐字移植（网关命名规则相同）——但以进程内原生 Python 函数提供，无需 MCP 网关，也无需一组 stdio 服务器进程。
- **智能体循环**：忠实移植 Mercor 的 `loop_truncated_tools_agent`——上下文裁剪、带拒绝/溢出机制的工具结果预算、token/成本统计、逐字节一致的消息模板。
- **LLM 层**：Mercor 的 `runner/utils/llm.py` 及其辅助模块在固定的上游提交上**逐字节原样内置（vendored）**——包含完整的各厂商请求适配（request surgery）、重试策略和缓存断点——其服务端基础设施通过轻量 shim 关闭，从不改写。上游改进通过重新同步引入，而非重新移植。

模型调用既可经由 OpenAI 兼容代理转发，**也可直接访问各厂商官方端点**——内置的请求适配层使两种模式行为完全一致，与 Mercor 的评测框架相同。只有 `python_code_execution`/`code_exec` 会用到容器：模型编写的代码始终在无网络的 Docker 沙箱中运行，预装软件包与 Archipelago 的代码服务器一致。

## 目录

- [快速上手 运行第一个任务](#快速上手-运行第一个任务)
- [组件](#组件)
  - [Environment 环境](#environment-环境)
  - [Native Tools 原生工具](#native-tools-原生工具)
  - [Agents 智能体](#agents-智能体)
  - [Grading 评分](#grading-评分)
- [Atoll 与 Archipelago 对比](#atoll-与-archipelago-对比)
- [本地开发](#本地开发)
- [许可证](#许可证)

---

## 快速上手 运行第一个任务

**预计耗时：约 10 分钟**

### 前置条件

- Docker——始终需要：模型编写的代码只在容器中运行
- [uv](https://docs.astral.sh/uv/)——仅本快速上手（从源码运行）需要；若本机没有 Python 3.12，
  uv 会自动安装（无需单独安装 Python）
- 模型访问，二选一：
  - 一个 OpenAI 兼容端点（例如 LiteLLM 代理）及其 API 密钥，**或**
  - 直连厂商：所用厂商的官方 API 密钥

> 只运行评测、不做开发？**只需 Docker**——无需 uv，也无需 Python：构建两个镜像后，用
> `scripts/run_eval.sh`（或你自己的流水线）启动每个评测。见 [容器化评测](./environment/README.md#containerized-evals)。

### 1. 配置环境变量

```bash
cd atoll
uv sync
cp .env.example .env
# 代理模式——编辑 .env：
#   LITELLM_BASE_URL=https://your-litellm-host/
#   LITELLM_API_KEY=sk-...
#   MODEL_ALIAS=gpt-4o-mini
# 直连模式——留空 LITELLM_BASE_URL，改为设置厂商密钥
#   (OPENAI_API_KEY=..., GEMINI_API_KEY=..., VOLCENGINE_API_KEY=..., ...)；
#   此时模型名使用 litellm 的厂商前缀，例如 "gemini/gemini-2.5-pro"。
# 两者并用——代理模式加上 ATOLL_DIRECT_MODELS=volcengine/（逗号分隔的模型名前缀，
#   类似 NO_PROXY），这些模型改为直连；网络关闭时须在 ATOLL_EGRESS_ALLOW 中列出两个主机。
#   直连调用的逐模型请求设置（厂商模型 ID、思考 / 推理强度）见 agents/runner/model_config.yaml。
```

### 2. 构建代码执行沙箱镜像（仅需一次）

```bash
scripts/build_sandbox_image.sh
# → atoll-sandbox:latest，预装与 Archipelago 代码服务器相同的软件包
#   (numpy, pandas, scipy, statsmodels, sympy, …)
```

### 3. 运行任务

```bash
uv run atoll-run --task <任务目录> --model <模型>
```

`<任务目录>` 是你自己的任务，采用下文[任务布局](#任务布局)中的任一种；`<模型>` 是你的端点或厂商提供的任意模型。

运行器将会：
1. 将任务的工作区模板复制到新的运行目录
2. 为初始状态生成快照
3. 挂载原生工具服务器并运行智能体循环
4. 为最终状态生成快照
5. 写出轨迹与结果

### 4. 查看结果

```bash
RUN=$(ls -td runs/<任务编号>/* | head -1)
cat $RUN/result.json | jq .status
cat $RUN/trajectory.jsonl | tail -1 | jq .
ls $RUN   # workspace/ initial_snapshot.tar.gz final_snapshot.tar.gz trajectory.jsonl result.json
```

---

## 组件

### Environment 环境

Environment 是一个纯 Python 库（`atoll_env`）加一个轻量 CLI——Archipelago 的无头 FastAPI 网关、S3 数据填充和 `/mcp` 端点均有意省略。

| Archipelago | Atoll |
|---|---|
| `POST /data/populate`（从 S3） | `atoll_env.workspace.create_workspace()`（从任务的 `workspace/` 模板） |
| `POST /data/snapshot`（tar.gz） | `atoll_env.snapshot.snapshot_workspace()`（相同的 tar.gz 布局） |
| `POST /apps`（热切换 MCP 配置） | `load_tools([...])`——选择本次运行挂载哪些工具服务器 |
| MCP 网关进程 | —（工具为进程内函数） |

`atoll_env.sandbox.ContainerSandbox` 是模型编写代码时**必需**的容器沙箱：Docker Desktop；`--network none`、限制内存、仅挂载工作区（挂载于 `/filesystem`，与 Archipelago 代码执行根目录一致）。不存在无沙箱的回退方案。容器化评测（机器上只需 Docker，无需安装 Python）中，每个评测以一对相互隔离的 runner + sandbox 容器运行（runner 网络默认**关闭**：只能访问 LLM 端点），可由你自己的流水线启动——UI、服务器、CI 或其他任何方式均可；见 [容器化评测](./environment/README.md#containerized-evals)。

> 更多细节见 [Environment README](./environment/README.md)。

### Native Tools 原生工具

`native_tools/` 取代了 Archipelago 的 `mcp_servers/`——这是目录布局中唯一的结构性改动。它与 Archipelago 的 11 个服务器模块一一对应：每个模块一个子文件夹，内含可导入的服务器包、独立的 `tests/` 和 README。已实现的模块逐字保留上游的工具名称、参数 schema、描述和输出文本；其余模块为描述原服务器的 README 占位：

```
native_tools/
├── core/              # atoll_tools 包：注册表、schema 生成、工作区上下文、LibreOffice 层（office.py）
├── filesystem/        # ✅ filesystem_server — 6 个工具
├── spreadsheets/      # ✅ sheets_server — 全部 12 个工具，含 edit_spreadsheet、filter_tab
├── pdfs/              # ✅ pdf_server — 5 个工具（PyMuPDF + reportlab）
├── code/              # ✅ code_execution_server — python_code_execution、code_exec（沙箱）
├── documents/         # ✅ docs_server — 全部 15 个工具（python-docx）
├── calendar/ chat/ edgar_sec/ fmp/ mail/ presentations/   # 📄 README 占位
├── README.md          # 模块索引及各工具说明（中文）
└── README.en.md       # 英文版
```

模型看到的工具名遵循 FastMCP 的网关规则，与 Archipelago 完全一致：挂载多个服务器时，工具以 `{server_name}_{tool_name}` 形式出现（例如 `filesystem_server_list_files`）。每个已移植的服务器还内置了 Archipelago 网关在固定上游提交上返回的原始 `tools/list` JSON（`published_schemas.json`）并原样提供，因此参数 schema——包括上游的扁平化处理、`(Optional)`/nullable 标注以及嵌套的 `input` 封装——逐字节一致；一致性由测试锁定。

**亮点：LibreOffice 办公组件层。** Archipelago 自带 LibreOffice，`sheets_server` 的 `read_tab` 和评分器都用它重算公式；Atoll 在 `atoll-runner` 镜像中安装同样的组件、逐字保留上游调用，因此 `read_tab` 与上游行为一致。`core/atoll_tools/office.py` 在此之上提供**强制全量重算**（LibreOffice 默认沿用文件缓存值，占位缓存会得到一片 0）与失败即报错的转换，供评分器和可选办公工具共用。可选办公工具（`sheets_server` 的 `recalculate_spreadsheet`、`docs_server` 的 `convert_document_to_pdf`）**默认关闭**，开启方式 `--office-tools on` / `task.toml` 的 `[atoll] office_tools` / `$ATOLL_OFFICE_TOOLS`；关闭时模型可见的工具表面与 Archipelago 完全一致（[详情](./native_tools/README.md#亮点libreoffice-办公组件层)）。

> 完整的工具对齐矩阵及尚未移植的 Archipelago 服务器列表，见 [原生工具 README](./native_tools/README.md)。

### Agents 智能体

Agents 组件对应 Archipelago 的 `agents/runner/`。核心循环（`agents/runner/loop.py`）改编自 Mercor 的 `loop_truncated_tools_agent`（Apache-2.0）：工具调用循环、工具结果截断至 200 行/32K 字符、在第一个不含工具调用的回复处结束。其下的 LLM 层是 Mercor 自己的代码，在固定上游提交上逐字节原样内置（含各厂商请求适配；未使用的基础设施通过 shim 关闭——见 `agents/runner/utils/PATCHES.md`）。

```
┌────────────────────────────────────────────────────────────┐
│                       Agents Runner                        │
│  runner/                                                   │
│  ├── main.py          CLI: load task, run loop, artifacts  │
│  ├── loop.py          Core loop (from Mercor's loop agent) │
│  ├── llm.py           LiteLLM (chat + Responses API) →     │
│  │                      any OpenAI-compatible endpoint     │
│  ├── responses.py     Chat ↔ Responses API conversion      │
│  ├── models.py        AgentRunInput / AgentTrajectoryOutput│
│  ├── registry.py      Agent registry                       │
│  └── providers/       ToolProvider boundary                │
│      ├── native.py    in-process registry (default)        │
│      └── mcp.py       MCP gateway interop (stub)           │
└────────────────────────────────────────────────────────────┘
```

`loop.py` 只依赖 `ToolProvider` 接口——将 `NativeToolProvider` 替换为未来指向真实 Archipelago 网关的 `McpToolProvider`，无需修改循环代码。

**停止条件**——Atoll 评测框架的默认值，可在 `task.toml` 中按任务覆盖，或通过 CLI/环境变量覆盖：`max_steps=250`、`timeout=3600s`、token 预算关闭（启用时，预算耗尽后会再给一个带警告的最终回合）。移植的循环本身保留上游的内部默认值（`100`/`10800s`/关闭）供库调用使用。

> 更多细节见 [Agents README](./agents/README.md)。

### Grading 评分

`atoll-grade --task <任务目录> --run <运行目录> --judge-model <模型>`（容器方式：`scripts/run_grade.sh`，渲染阶段无网络、评审阶段只能访问评审端点）。按评分标准逐条调用 LLM 评审（评审模型由你选择），评审对象为智能体新增或修改的文件；每次运行一个加权得分率，每个模型取多次运行的平均分。文件由 Archipelago 自己的提取器（逐字节 vendored）渲染。与 Archipelago 不同，Atoll 的评审会看到任务附带的、由专家准备的参考答案（golden），并按用户实际看到的样子评判表格：每个工作簿在渲染前先用 LibreOffice 在本地强制全量重算（重算失败即终止评分），超过 500 行的工作表另附由程序基于全部数据行计算的数据概要。详见 [grading/README.md](./grading/README.md)。

---

## Atoll 与 Archipelago 对比

| | Archipelago | Atoll |
| --- | --- | --- |
| 工具传输方式 | MCP 网关 + 每个工具族一个 stdio 服务器 | 进程内原生 Python 函数 |
| 模型可见的工具表面 | 经 OpenAI function calling 暴露的 MCP 服务器 | 完全相同（逐字移植，网关命名相同） |
| 每个任务的内存开销 | 约 400MB（MCP 网关 + 服务器进程） | 约 0（单个 Python 进程） |
| 容器 | 整个环境运行于 Docker | 宿主机运行：仅代码沙箱使用 Docker；容器化评测：每个评测一个 runner + 无网络 sandbox（`network` 关闭时另加 egress 代理） |
| 网络 | [APEX-Agents 论文](https://arxiv.org/abs/2601.14242)写明：*"Web search is turned off for the benchmark to keep evaluations reproducible"*（§3.1）；我们在开源仓库中未发现容器层面的出网限制 | **并非 Mercor 的实现**——Atoll 将“关闭网络搜索”理解为“不获取任何外部信息”（若可自由联网，智能体能获取其可达的任何信息），因此容器化评测提供 `network` 设置（与 `max_steps` 同类），默认**关闭**：runner 只能经每个评测专属的 egress 代理访问白名单中的 LLM 端点；代码沙箱始终无网络（[详情](./environment/README.md#network-setting)） |
| 任务格式 | Harbor 任务包（`task.toml`、`instruction.md`、快照） | 相同布局 |
| 智能体循环 | `loop_truncated_tools_agent` | 忠实移植：裁剪、预算、溢出、逐字节一致的模板（见 NOTICE） |
| 系统提示词 | 所有任务、所有模型共用同一提示词（APEX-Agents 论文附录 D.2），存于文档中，由调用方写入初始消息 | 同样只有一个默认提示词：`atoll_agents.md`（以 Mercor 原文为基础的草稿；原文另以 `apex_agents.md` 逐字保留作参考），位于 [`agents/runner/system_prompts/`](./agents/runner/system_prompts/)；可全局或按任务覆盖（按任务优先） |
| 评分 | 按评分标准逐条调用 LLM 评审（`output_llm`，Gemini 3 Flash，thinking 设为 low），提供标准与改动文件（本地提取器，或远程解析服务 Reducto）；报告未加权的标准通过率与 Pass@1 | 逐条 LLM 评审（评审模型由你选择）；**评审还会看到参考答案（golden）**；同一个本地提取器（逐字节 vendored），但工作簿**强制 LibreOffice 全量重算**、失败即终止，不用远程解析；只有一个**加权**得分率，没有确定性检查——[详情](./grading/README.md#与-mercor-评分的差异) |
| 办公组件（LibreOffice） | 环境镜像安装 `libreoffice-calc`/`libreoffice-core`（`read_tab` 重算公式），评分器另带一份 | `atoll-runner` 安装同样的组件，上游调用逐字保留；另有强制全量重算的 `atoll_tools.office`，以及默认关闭的可选办公工具（[详情](./native_tools/README.md#亮点libreoffice-办公组件层)） |
| LLM 客户端层 | `runner/utils/llm.py`：厂商请求适配、重试、缓存断点 | 同一批文件，在固定提交上逐字节内置；服务端基础设施通过 shim 关闭（`agents/runner/utils/PATCHES.md`） |
| 模型路由 | 直连厂商或经其代理 | 相同：代理或直连，两种方式请求适配一致 |
| 重试归属 | 评测客户端负责重试；代理直通 | 相同（为 Atoll 部署的网关应将重试次数设为 0） |
| 仓库布局 | `mcp_servers/` | 更名为 `native_tools/`；其余保持一致 |
| 许可证 | Apache-2.0 | Apache-2.0 |

---

## 本地开发

```bash
uv sync                                    # 安装 uv workspace（3 个包）
uv run pytest                              # 运行测试套件
uv run ruff check .                        # 代码检查
```

### 任务布局

**任务包**（Harbor 风格）：`task.toml`（元数据、`[agent]` 停止条件、`[atoll]` 设置）+ `instruction.md`（用户消息）+ `workspace/`（智能体的初始文件），评分另需 `rubrics.json` + `reference/`（参考答案，仅供评审）。

`atoll-run` 与 `scripts/run_eval.sh` 也接受**交付任务**：`prompt.md`（用户消息，智能体看不到这个文件）、
`filesystem/`（挂载为 `/filesystem`）、`reference/`（`rubrics.json` + `outputs/`，仅供评审、从不挂载），
以及可选的 `system_prompt.md` 和 `task.toml`。其运行结果写入 `results/<model>/run_<n>/`（`<model>` 为模型别名的最后一段，或用 `--model-name` 指定——例如把服务商的完整模型 ID 归入更短的名称；
`n` 默认取下一个空号，也可用 `--out`/`--output` 指定目录），以 `agent_outputs/`（智能体新增或修改的文件）代替 `workspace/`。

容器化评测：先运行 `scripts/build_runner_image.sh` 与 `scripts/build_sandbox_image.sh`，再用 `scripts/run_eval.sh`（每个评测一对隔离容器；见 [Environment README](./environment/README.md#containerized-evals)）。

---

## 许可证

Apache-2.0（见 [LICENSE](./LICENSE)）。

部分代码改编自或逐字内置于 Mercor Intelligence 的 [Archipelago](https://github.com/Mercor-Intelligence/archipelago)（Apache-2.0）——主要包括 `agents/runner/loop.py`、`agents/runner/utils/` 下的 LLM 层（逐字节一致的文件列于 `agents/runner/utils/PATCHES.md`）以及 `native_tools/` 各服务器模块中的工具实现——见 [NOTICE](./NOTICE)。Atoll 的 PDF 工具依赖 [PyMuPDF](https://pymupdf.readthedocs.io/)（AGPL-3.0），与 Archipelago 的 PDF 服务器使用同一引擎：Atoll 自身代码仍为 Apache-2.0，而 PyMuPDF 的许可条款适用于组合作品的运营方——内部评测使用没有问题；基于 Atoll 对外提供服务的机构应评估 AGPL 义务，或向 Artifex 购买商业许可。
