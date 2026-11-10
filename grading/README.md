# grading（评分）

**简体中文** | [English](./README.en.md)

Atoll 的评分器：对智能体产出的文件和任务附带的参考答案（golden）文件做文本渲染，**按评分标准逐条**调用一次 LLM 评审，再按权重折算分数。边界与 Archipelago 的 `grading/` 一致——读取一次已完成的运行、写出分数，从不触碰智能体循环。

## 用法

```bash
# 单进程（主机或任何装有 LibreOffice 的容器）
atoll-grade --task <任务目录> --run <运行目录> --judge-model <模型> [--output score.json]

# 容器方式，参考启动脚本（渲染阶段无网络；评审阶段经出口代理）
scripts/run_grade.sh --task <任务目录> --run <运行目录> --out <评分目录> \
    --judge-model <模型> --env-file .env
```

- **评审模型**：由你选择——任何 LiteLLM 模型均可：设置了 `LITELLM_BASE_URL` / `LITELLM_API_KEY` 时，调用发往该 OpenAI 兼容端点（别名写作 `litellm_proxy/<别名>`）；未设置时 litellm 直连服务商（带服务商前缀的模型名 + 服务商密钥）。默认取 `$ATOLL_JUDGE_MODEL`。
- `--concurrency`（默认 4，`$ATOLL_JUDGE_CONCURRENCY`）、`--timeout`（单次调用，600 秒）、`--judge-extra-args`（JSON，如 `'{"reasoning_effort": "high"}'`，`$ATOLL_JUDGE_EXTRA_ARGS`）。
- 分阶段：`--render-only <目录>` 写出 `<目录>/rendered.json`；`--from-rendered <目录>` 对其评审；两者都不加时在一个进程内依次完成。
- 退出码：`0` 评分完成；`3` 有工作簿无法重算；`4` 有评分项无法评审。只有全部评分项都完成才算评分——返回 3 或 4 时不写 `score.json`。

调用经由 Atoll vendored 的 LLM 层（`runner.llm.generate_response`），路由方式、针对各服务商的请求处理和重试策略都与智能体循环相同。每次调用带 `x-atoll-task` / `x-atoll-trajectory`（`grade/<运行名>`）请求头，便于按任务统计花费。

## 输入

| | Atoll 布局 | 交付布局 |
|---|---|---|
| 任务 | `task.toml` + 题干文件；`[atoll] rubrics`（默认 `rubrics.json`）与 `[atoll] reference_dir`（默认 `reference/`，其中除评分标准文件外都是参考答案文件） | `prompt.md`（或 `workspace/prompt.md`）、`reference/rubrics.json`、`reference/outputs/`，可选 `task.toml`（编号与运行设置）；编号依次取自 `task.toml` 的 `[metadata] id`、`metadata.json` 的 `task_id` |
| 运行 | `atoll-run` 的输出：`result.json`（最终回复），以及智能体**新增或修改**的文件——即 `initial_snapshot.tar.gz` 与 `final_snapshot.tar.gz` 的差异，与 Archipelago 的评分方式相同 | 同左的 `atoll-run` 输出；或 `agent_outputs/` 与 `cli.json`（最后一个 `result` 事件即最终回复） |

评分项字段：`rubric_number`、`rubric_tag`、`rubric_weight`、`rubric_detail`。点开头的文件和 Office 锁文件（`~$…`）会被忽略。

## 评分流程

- **每条评分标准一次评审调用**，不带工具——与 Archipelago 的 `output_llm` verifier 相同。评审看到：任务题干、最终回复、渲染后的候选文件与参考答案文件、本条标准点名的大表行，以及放在最后的评分项本身（因此评分项之前的内容是每次运行共享的前缀，服务商可以缓存）。
- **二元判定**，评分项权重的符号决定规则：权重 > 0 为得分项（全部要求满足 → 1），权重 < 0 为扣分项（出现任一所述错误 → 1），权重 = 0 为否决项（条件成立 → 整次运行得 0 分）。
- **扣分项的方向显式化。** 出题人对扣分条件的写法不一（`fire 当且仅当 X`、`pass 当且仅当 X`，或写出*正确*答案再接 `则得-N分`）。评分标准按原样使用，不做人工改写；对每个扣分项，评审必须先写明本项惩罚的是什么错误（`deduction_for`），以及参考答案是否存在该错误（`reference_has_error`，必须为 `false`），然后才给出判定——方向读反时，会表现为参考答案被扣分。缺少任一字段的扣分项回答会被要求重答。
- 无法解析的输出最多重问 3 次；LLM 调用失败由 vendored 层重试。提示词见 [`prompts/`](./prompts/)。

## 渲染

评分按用户打开文件时实际看到的内容来判。

**Archipelago 的提取器，原样 vendored。** 文件由上游评分器自己的提取层渲染（`runner/utils/file_extraction`，在 pin 定提交处逐字节一致——见 [`agents/runner/utils/PATCHES.md`](../agents/runner/utils/PATCHES.md)）：每个可见工作表一个数值网格，然后是 `=== Number Formats ===`（`A1: 1.50 [fmt: 0.00]`）与 `=== Formulas ===`（`C9: =C7-C8`，即原样的公式文本）两个区块；文档、演示文稿和 PDF 渲染为文本与表格。每个文件以 `=== File: <文件名> ===` 开头，工作簿经过重算时标注 `(recalculated by LibreOffice)`。

**Atoll 的补丁**（运行时生效，位于 `atoll_grading/render.py`；不修改任何 vendored 文件）：

1. **强制全量重算。** 上游只在存在无缓存值的公式时才重算，而 LibreOffice 会沿用已有的缓存值——带占位结果的工作簿（xlsxwriter 给每个公式缓存 0 并设置 `fullCalcOnLoad`）就会按一片 0 来评分。Atoll 在存在无缓存值的公式、*或*文件要求打开时重算的情况下都会重算，并且总是全量重算（`atoll_tools.office`）。
2. **重算失败即终止评分**，不退回缓存值。
3. **显示所有非“常规”的数字格式**，而不只是货币/百分比/千分位格式：精度要求（保留两位小数）按格式判定。

**大表**（非空行超过 500 行）只展示前 30 行和后 5 行，另附由程序基于全部数据行计算的 `【数据概要】`：行数、不同值与重复值、低基数列的取值分布、列合计、最小/最大值、公式数量与错误值（合计行不计入，并单独注明）。评分项点名（按编码或键）的行会连同公式完整列在评分项之前。因此计数、合计、唯一性和覆盖率都按全部行判定，而不是按可见的样本。

**公式错误**：单元格显示错误值，或其公式文本不是合法的 Excel 公式（`=` 后接自然语言、全角运算符、`Sheet.A1` 式引用、缺少操作数）时计为错误——即使错误只在重算后才出现。对*合法*公式重算后才出现的错误视为 LibreOffice 兼容性问题，不计。

## 容器

`scripts/run_grade.sh` 使用 `atoll-runner` 镜像评分（入口 `atoll-grade`；LibreOffice 安装在该镜像中，与 Archipelago 的环境镜像一致——除非某次运行开启了[可选办公工具](../native_tools/README.md#亮点libreoffice-办公组件层)，否则没有任何对模型可见的工具能调用它）：

- **render（渲染）**——解析不可信的、由智能体写出的文件：`--network none`，不带任何凭据，任务与运行目录只读挂载。
- **judge（评审）**——只读取 `rendered.json`；唯一的网络是本次评分专属的 Docker `--internal` 网络，出口是只放行评审端点的出口代理（`$ATOLL_EGRESS_ALLOW`，否则为 `LITELLM_BASE_URL` 的主机）。

两个容器都沿用评测容器的加固措施（只读根文件系统、无 capabilities、非 root、资源上限、不挂载 Docker socket）。

## 分数

每次运行的 `score.json`：

| 字段 | 含义 |
|---|---|
| `score` | **分数**——加权得分率：(通过的得分项权重之和 − 触发的扣分项权重绝对值之和) ÷ 得分项权重之和，截断到 0–100；否决项触发时为 0 |
| `vetoed` | 是否有权重为 0 的否决项触发 |
| `items` | 每项：`rubric_number`、`rubric_weight`、`verdict`、`rationale`（中文）；扣分项另有 `deduction_for`、`reference_has_error` |
| `usage` | 评审调用次数与 token 数 |

每个模型：**`mean_score`** = 其各次运行 `score` 的平均值（默认每模型一次运行）。
`atoll-grade --summarize <task_dir>` 读取所有 `results/<model>/run_<n>/score.json`，写出
`<task_dir>/results/summary.json`（每个模型的各次运行及其 `score`，以及 `mean_score`）。

## 与 Mercor 评分的差异

- **评审始终能看到参考答案（golden）。** APEX-Agents 的设置中，评审 *"takes in the task prompt, the agent output, a log of the changes induced by the agent's actions"*（§4.2）；Archipelago 的评分器可以为某条标准附加参考文件（`artifacts_to_reference`），但仅限配置了的标准。Atoll 的任务都附有专家准备的参考答案，因此每条评分项都会附上：它们能更可靠地裁定开放式和判断类评分项，也让参考答案的准备工作真正发挥作用。评分标准本身仍应尽量自包含。
- **只有一个加权分数。** Archipelago 报告的是未加权的标准通过比例（以及 Pass@1）。Atoll 的评分标准有意设置了权重——关键判断步骤的权重高于常规检查，扣分项减分，否决项使整次运行得 0 分——因此 Atoll 只报告加权得分率。
- **评分标准就是全部评分。** 评审之外没有确定性检查——与 APEX-Agents 相同，每条标准都由 LLM 评审（§4.2）；Archipelago 的其他 verifier 类型服务于其他基准。
- **同一个提取器，严格地在本地重算。** Atoll 用 Archipelago 自己的提取代码渲染，但强制 LibreOffice 全量重算，从不退回过期的缓存值；也不使用上游可调用的远程解析服务（Reducto），任务文件不会离开本机。只读缓存值的评审评的是文件最后*保存*时的样子，而不是用户看到的样子：只有公式没有缓存值的工作簿、占位缓存以及在 Excel 中非法的公式，都会因此被误判。
- **评审模型由你选择。** APEX-Agents 使用 Gemini 3 Flash（*"we use Gemini 3 Flash, with thinking set to low, as the judge."*，[arXiv:2601.14242](https://arxiv.org/abs/2601.14242)，§4.2）。请按评分标准的工作负载选择评审模型：条目多、需对多工作表工作簿做数值核对的评分标准，宜用推理能力强的模型。

## 难度等级

`L1`（最易）– `L2` – `L3`（最难），依据各模型的平均分划分。阈值**暂定**，放在配置中而非代码里。

## 代码结构

| 路径 | 作用 |
|---|---|
| `atoll_grading/inputs.py` | 任务与运行的加载（两种布局）、快照差异 |
| `atoll_grading/render.py` | vendored 提取器 + Atoll 补丁、大表数据概要、点名行 |
| `atoll_grading/judge.py` | 提示词组装、逐条调用、判定解析 |
| `atoll_grading/score.py` | 加权折算、按模型的 `mean_score` |
| `atoll_grading/main.py` | `atoll-grade` 命令行 |
| `prompts/` | 评审系统提示词 + 单条评分项的用户消息 |
| `tests/` | 输入、渲染（无 `soffice` 时跳过 LibreOffice 相关测试）、解析、折算 |

## TODO

- [ ] 在现有题库（约 100 题）上标定 L1–L3 阈值——基于用本评审重新评分后的分数。
- [ ] 图表图片：提取器可以把图表渲染成图片（LibreOffice → PDF → poppler）；评审目前只看文本。
