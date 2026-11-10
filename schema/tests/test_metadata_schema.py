"""The task metadata schema: valid as JSON Schema 2020-12, accepts one Agent and one QA
sample, and tells the two kinds apart."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMA = json.loads((Path(__file__).parents[1] / "metadata.schema.json").read_text(encoding="utf-8"))

AGENT = {
    "internal_id": "0192f8a1-0000-7000-8000-000000000001",
    "task_id": "AG-0001-ACC-v1",
    "framework": "atoll",
    "subject": "会计",
    "tags": "合并报表",
    "prompt": "Build the consolidation workbook.",
    "common_errors": "Eliminations missed.",
    "knowledge_points": "IFRS 10",
    "author": "Teraverse",
    "date": "2026-11-10",
    "difficulty": "L2",
    "evaluations": {"model-a": {"runs": [{"score": 62.5, "date": "2026-11-10"}], "mean_score": 62.5}},
}

QA = {
    "task_id": "QA-0001-MIX-v1",
    "subject": "综合",
    "tags": "CFA会计",
    "qa_type": "客观",
    "question_type": "CA",
    "prompt": "Compute FCFF.",
    "reference_answer": "264.0 M",
    "rubrics": [{"rubric_number": 1, "rubric_tag": "C", "rubric_weight": 5, "rubric_detail": "FCFF = 264.0 M"}],
    "common_errors": "Starts from reported EBIT.",
    "knowledge_points": "FCFF",
    "author": "Teraverse",
    "date": "2026-11-08",
    "difficulty": "L2",
    "evaluations": {
        "model-a": {
            "runs": [{"response": "FCFF = 258 M", "score": 54.0, "correct": 0, "date": "2026-11-10"}],
            "mean_score": 54.0,
        }
    },
}


def errors(doc, ref=None):
    """Validation errors against the whole schema, or against one of its $defs."""
    schema = SCHEMA
    if ref is not None:
        schema = {k: v for k, v in SCHEMA.items() if k != "oneOf"} | {"$ref": f"#/$defs/{ref}"}
    return list(Draft202012Validator(schema).iter_errors(doc))


def test_the_schema_is_valid_json_schema():
    Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("doc,kind", [(AGENT, "agent"), (QA, "qa")])
def test_each_sample_is_exactly_its_own_kind(doc, kind):
    assert errors(doc) == []
    assert errors(doc, kind) == []
    other = "qa" if kind == "agent" else "agent"
    assert errors(doc, other)


def test_rubrics_json_validates_on_its_own():
    assert errors(QA["rubrics"], "rubrics") == []
    assert errors([{"rubric_number": 1, "rubric_weight": 5}], "rubrics")


@pytest.mark.parametrize(
    "doc,change",
    [
        (AGENT, lambda d: d.pop("framework")),
        (AGENT, lambda d: d.update(difficulty="L4")),
        (AGENT, lambda d: d.update(extra="x")),
        (AGENT, lambda d: d["evaluations"]["model-a"]["runs"][0].update(score="62.5%")),
        (QA, lambda d: d.pop("reference_answer")),
        (QA, lambda d: d.update(framework="atoll")),  # both kinds at once
        (QA, lambda d: d["evaluations"]["model-a"]["runs"][0].pop("correct")),  # 客观 needs it
        (QA, lambda d: d.update(date="10/11/2026")),
    ],
)
def test_rejects(doc, change):
    bad = copy.deepcopy(doc)
    change(bad)
    assert errors(bad)


def test_a_subjective_qa_needs_no_correct_verdict():
    doc = copy.deepcopy(QA)
    doc["qa_type"] = "主观"
    doc["evaluations"]["model-a"]["runs"][0].pop("correct")
    assert errors(doc) == []
