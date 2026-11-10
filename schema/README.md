中文 | [English](./README.en.md)

# 任务元数据 Schema

[`metadata.schema.json`](./metadata.schema.json) 是交付任务元数据的 JSON Schema（draft 2020-12），一个文件同时描述两类任务：

| 类别 | 文件 | 定义 | 识别方式 |
|---|---|---|---|
| Agent 任务 | 任务目录下的 `metadata.json` | `#/$defs/agent` | 含 `framework: "atoll"` |
| 问答（QA）任务 | 单个 `<task_id>.json` | `#/$defs/qa` | 含 `qa_type` |

顶层 schema 要求文档恰好属于其中一类（`oneOf`）；也可以只引用其中一类的定义。Agent 任务的 `reference/rubrics.json` 用 `#/$defs/rubrics` 校验。

## 字段

| 字段 | Agent | QA | 说明 |
|---|---|---|---|
| `internal_id` | ✓ | | 作者内部编号（GUID） |
| `task_id` | ✓ | ✓ | 任务编号；Teraverse 的约定见下 |
| `framework` | ✓ | | 固定为 `"atoll"` |
| `subject` | ✓ | ✓ | 科目，如 `会计`、`综合` |
| `tags` | ✓ | ✓ | 科目副标签（字符串） |
| `qa_type` | | ✓ | `客观` / `主观` |
| `question_type` | | ✓ | `SC` 单选、`MC` 多选、`CA` 计算、`NA` 无 |
| `prompt` | ✓ | ✓ | 任务文本，即运行时的用户消息 |
| `reference_answer` | | ✓ | 标准答案（Agent 任务的参考答案在 `reference/outputs/`） |
| `rubrics` | | ✓ | 评分标准（Agent 任务在 `reference/rubrics.json`） |
| `common_errors` | ✓ | ✓ | 典型错误 |
| `knowledge_points` | ✓ | ✓ | 涉及知识点 |
| `author` | ✓ | ✓ | 发布者 |
| `date` | ✓ | ✓ | 完成日期，`YYYY-MM-DD` |
| `difficulty` | ✓ | ✓ | `L1`（最易）– `L3`（最难），按最佳模型的平均分判定 |
| `evaluations` | ✓ | ✓ | 每个模型：`{"runs": [...], "mean_score": …}` |

`evaluations` 的每次运行：Agent 为 `{score, date}`；QA 为 `{response, score, correct, date}`，`correct`（0/1，最终答案是否与标准答案一致）仅客观题必填。`score` 为加权得分率（百分数，数值）。

评分标准每一项：`rubric_number`、`rubric_tag`、`rubric_weight`（> 0 得分项，< 0 扣分项，0 否决项）、`rubric_detail`。

## 任务编号约定

Teraverse 发布的任务使用 `AG-0001-ACC-v1`（Agent）/ `QA-0001-ACC-v1`（QA）：{类别}-{4 位序号，两类各自编号}-{科目缩写}-v{版本号}。科目缩写：`ACC` 会计、`AUD` 审计、`CFM` 财管、`TAX` 税法、`MIX` 综合、`OTH` 其他。Atoll 本身接受任意编号，schema 不强制此格式。

## 校验

```bash
uv run python -c "
import json, sys
from jsonschema import Draft202012Validator
schema = json.load(open('schema/metadata.schema.json'))
for e in Draft202012Validator(schema).iter_errors(json.load(open(sys.argv[1]))):
    print(e.json_path, e.message)
" path/to/metadata.json
```

无输出即通过。测试：`uv run pytest schema/tests`。
