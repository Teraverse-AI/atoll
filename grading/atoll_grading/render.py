"""Render a run's files (and the reference files) as text for a tool-less judge.

The extraction itself is Archipelago's grader code, vendored byte-identical in
``runner/utils/file_extraction`` (see agents/runner/utils/PATCHES.md): per sheet a
grid of values, then ``=== Number Formats ===`` and ``=== Formulas ===`` blocks;
documents, PDFs and presentations as their text. Atoll changes its behaviour only
through the runtime patches in :func:`apply_patches` (module attributes are
replaced; no vendored file is edited) and adds one thing on top — summaries for
large sheets:

1. **Forced full recalculation.** Upstream recalculates a workbook only when some
   formula has no cached value, and LibreOffice then keeps any cached values it
   finds. A workbook written with placeholder results (xlsxwriter: 0 for every
   formula plus ``fullCalcOnLoad``) is therefore graded on zeros. Atoll recalculates
   whenever a formula lacks a value *or* the file asks for recalculation on open,
   and always fully (``atoll_tools.office``).
2. **A failed recalculation raises** instead of falling back to cached values.
3. **Every non-General number format is shown**, not only currency/percent/grouped
   ones — precision requirements (两位小数) are judged by the format.
4. **Large sheets** (more than ``FULL_ROWS`` rows) are shown as their first and last
   rows plus a program-computed 【数据概要】 over every data row; rows a rubric item
   names are rendered in full just before that item (:meth:`Rendering.related`).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from atoll_tools import office
from runner.utils.file_extraction.methods import local_extractor as _le

FULL_ROWS = 500  # sheets with up to this many non-empty rows are rendered in full
HEAD, TAIL = 30, 5  # larger sheets: first/last non-empty rows + summary
TOP_VALUES = 12  # value counts shown for low-cardinality text columns
MAX_RELATED = 20  # rows pulled in per item per sheet
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-./]{3,}")
_SHEET_SUFFIXES = {".xlsx", ".xlsm"}

RecalcError = office.OfficeError

# ---------------------------------------------------------------- runtime patches

# Recalculated bytes by sha256 of the input, so the large-sheet pass reads the very
# values the vendored extractor rendered.
_RECALCULATED: dict[str, bytes] = {}
_UPSTREAM_IS_NUMERIC_DISPLAY_FORMAT = _le._is_numeric_display_format
_DATE_TIME_TOKENS = re.compile(r"[ymdhs]", re.IGNORECASE)
_BRACKETS = re.compile(r"\[[^\]]*\]|\"[^\"]*\"")


async def _evaluate_with_forced_recalculation(file_bytes: bytes, suffix: str = ".xlsx") -> bytes:
    """Stand-in for upstream ``evaluate_excel_formulas_with_libreoffice``: forced, raising."""
    with tempfile.TemporaryDirectory(prefix="atoll-grade-recalc-") as tmp:
        src = Path(tmp) / f"workbook{suffix}"
        src.write_bytes(file_bytes)
        out = await asyncio.to_thread(office.recalculate, src, Path(tmp) / "out")
        data = out.read_bytes()
    _RECALCULATED[hashlib.sha256(file_bytes).hexdigest()] = data
    return data


def _needs_recalculation(source: Path | bytes, source_name: str = "") -> bool:
    """Stand-in for upstream ``_xlsx_has_uncached_formulas``: also True on fullCalcOnLoad."""
    try:
        if isinstance(source, bytes):
            with tempfile.NamedTemporaryFile(suffix=".xlsx") as fh:
                fh.write(source)
                fh.flush()
                return office.needs_recalculation(fh.name)
        return office.needs_recalculation(source)
    except Exception:
        return True  # upstream's rule: on uncertainty, recalculate


def _is_numeric_display_format(number_format: str) -> bool:
    """Upstream's check, widened to every non-General, non-date/time numeric format."""
    if _UPSTREAM_IS_NUMERIC_DISPLAY_FORMAT(number_format):
        return True
    if not number_format or number_format in ("General", "@"):
        return False
    core = _BRACKETS.sub("", number_format)
    return "0" in core and not _DATE_TIME_TOKENS.search(core)


def apply_patches() -> None:
    """Install Atoll's patches into the vendored local extractor (idempotent)."""
    _le.evaluate_excel_formulas_with_libreoffice = _evaluate_with_forced_recalculation
    _le._xlsx_has_uncached_formulas = _needs_recalculation
    _le._is_numeric_display_format = _is_numeric_display_format


apply_patches()

# ---------------------------------------------------------------- large sheets


def _fmt(v) -> str:
    if isinstance(v, float):
        if v != 0 and abs(v) < 1e-4:
            return f"{v:.6g}"
        return f"{v:.6f}".rstrip("0").rstrip(".")
    return str(v)


@dataclass
class _LargeSheet:
    file: str
    title: str
    rows: list[tuple[int, list[tuple[str, object, str | None]]]]  # (row, [(coord, value, formula)])
    hidden_from: int  # first row number not shown in the sheet text
    hidden_to: int

    def row_text(self, row: int, cells) -> str:
        values = "\t".join(_fmt(v) for _, v, _ in cells if v is not None)
        formulas = "; ".join(f"{c}: {f}" for c, _, f in cells if f)
        return f"row {row}: {values}" + (f"  [formulas: {formulas}]" if formulas else "")

    def related(self, item_text: str) -> list[str]:
        keys = set(_CODE.findall(item_text))
        if not keys:
            return []
        hits = []
        for row, cells in self.rows:
            if self.hidden_from <= row <= self.hidden_to and any(
                isinstance(v, str) and v in keys for _, v, _ in cells
            ):
                hits.append(self.row_text(row, cells))
                if len(hits) >= MAX_RELATED:
                    break
        return hits


def _load_rows(values_wb, formulas_wb, title: str):
    ws, wf = values_wb[title], formulas_wb[title]
    rows = []
    for row in ws.iter_rows():
        cells = []
        for c in row:
            f = wf[c.coordinate].value
            f = f if isinstance(f, str) and f.startswith("=") else None
            if c.value is not None or f:
                cells.append((c.coordinate, c.value, f))
        if any(v is not None for _, v, _ in cells):
            rows.append((row[0].row, cells))
    return rows


def _summary(rows) -> str:
    hi = max(range(min(10, len(rows))), key=lambda i: sum(isinstance(v, str) for _, v, _ in rows[i][1]))
    header = {re.sub(r"\d+", "", c): str(v) for c, v, _ in rows[hi][1]}
    body = rows[hi + 1:]

    def is_total(cells) -> bool:
        return any(isinstance(v, str) and re.search(r"合计|总计|^total", v, re.I) for _, v, _ in cells)

    totals = [r for r, cells in body if is_total(cells)]
    body = [(r, cells) for r, cells in body if not is_total(cells)]
    cols: dict[str, list] = {}
    for _, cells in body:
        for coord, v, f in cells:
            cols.setdefault(re.sub(r"\d+", "", coord), []).append((f, v))
    tot = f"；合计/总计行（第 {', '.join(map(str, totals))} 行）不计入以下统计" if totals else ""
    out = [f"【数据概要】表头为第 {rows[hi][0]} 行；其下数据行 {len(body)} 行{tot}。"
           "以下统计由程序基于全部数据行计算（数值为重算后的值）。"]
    for col in sorted(cols, key=lambda s: (len(s), s)):
        vals = cols[col]
        nums = [v for _, v in vals if isinstance(v, int | float) and not isinstance(v, bool)]
        texts = [str(v) for _, v in vals if isinstance(v, str)]
        errs = Counter(t for t in texts if t.startswith("#"))
        nform = sum(1 for f, _ in vals if f)
        parts = [f"列 {col}「{header.get(col, '')}」: 非空 {sum(v is not None for _, v in vals)}"]
        if nform:
            parts.append(f"公式 {nform} 个（首例 {next(f for f, _ in vals if f)}）")
        if nums:
            parts.append(f"数值 {len(nums)} 个, 合计 {sum(nums):.4f}, 最小 {_fmt(min(nums))}, 最大 {_fmt(max(nums))}")
        if texts:
            distinct = Counter(texts)
            parts.append(f"文本 {len(texts)} 个, 不同值 {len(distinct)}")
            if len(distinct) == len(texts) and len(texts) > 50:
                parts.append("无重复")
            elif len(distinct) > 0.9 * len(texts) and len(texts) > 50:
                dup = [k for k, n in distinct.items() if n > 1]
                parts.append(f"重复值 {len(dup)} 个（如 {dup[:5]}）")
            elif len(distinct) <= 60:
                parts.append("取值分布 " + "; ".join(f"{k:.60}×{n}" for k, n in distinct.most_common(TOP_VALUES))
                             + (" …" if len(distinct) > TOP_VALUES else ""))
        if errs:
            parts.append(f"错误值 {dict(errs)}")
        out.append("  " + "，".join(parts))
    return "\n".join(out)


def _large_sheet_text(sheet: _LargeSheet, upstream_text: str, rows) -> str:
    """Upstream's sheet text cut to head/tail rows, plus the summary block."""
    head, tail = rows[:HEAD], rows[-TAIL:]
    shown = {r for r, _ in head} | {r for r, _ in tail}
    sections = upstream_text.split("\n\n=== ")
    grid = sections[0].split("\n")
    title_line, grid_lines = grid[0], grid[1:]
    # Upstream emits one grid line per non-empty row, in row order.
    if len(grid_lines) == len(rows):
        kept = grid_lines[:HEAD] + [None] + grid_lines[-TAIL:]
    else:  # fall back to Atoll's own rendering of the shown rows
        kept = [sheet.row_text(r, c) for r, c in head] + [None] + [sheet.row_text(r, c) for r, c in tail]
    marker = (f"…（第 {sheet.hidden_from}–{sheet.hidden_to} 行共 {len(rows) - HEAD - TAIL} 个非空行未逐行展示，"
              "见下方【数据概要】；评分项点名的行会在评分项前单独列出）…")
    lines = [title_line, f"[rows {head[0][0]}–{head[-1][0]}]"]
    lines += [marker if k is None else k for k in kept[:HEAD]]
    lines += [marker, f"[rows {tail[0][0]}–{tail[-1][0]}]"] + [k for k in kept[HEAD + 1:]]
    out = "\n".join(lines)
    for section in sections[1:]:
        name, _, body = section.partition(" ===\n")
        keep = [ln for ln in body.split("\n")
                if (m := re.match(r"[A-Z]+(\d+):", ln)) and int(m.group(1)) in shown]
        out += f"\n\n=== {name} === (shown rows only; 【数据概要】 covers all rows)\n" + "\n".join(keep)
    return out + "\n\n" + _summary(rows)


# ---------------------------------------------------------------- rendering


@dataclass
class Rendering:
    text: str
    large_sheets: list[_LargeSheet] = field(default_factory=list)

    def related(self, item_text: str) -> str:
        out = []
        for sheet in self.large_sheets:
            hits = sheet.related(item_text)
            if hits:
                out.append(f"《{sheet.file}》「{sheet.title}」中被本评分项点名的行：\n" + "\n".join(hits))
        return "\n".join(out)


async def _render_workbook(path: Path, extractor, name: str) -> tuple[str, list[_LargeSheet]]:
    raw = path.read_bytes()
    recalculated = _needs_recalculation(path, name)
    extracted = await extractor.extract_from_file(path, include_images=False)
    note = " (recalculated by LibreOffice)" if recalculated else ""
    sheets = list(extracted.sub_artifacts or [])
    values_src = _RECALCULATED.get(hashlib.sha256(raw).hexdigest()) if recalculated else None
    need_rows = any(s.content.count("\n") > FULL_ROWS for s in sheets)
    large: list[_LargeSheet] = []
    if need_rows:
        values_wb = openpyxl.load_workbook(io.BytesIO(values_src) if values_src else path, data_only=True)
        formulas_wb = openpyxl.load_workbook(path, data_only=False)
        for i, sub in enumerate(sheets):
            if sub.title not in values_wb.sheetnames:
                continue
            rows = _load_rows(values_wb, formulas_wb, sub.title)
            if len(rows) <= FULL_ROWS:
                continue
            sheet = _LargeSheet(name, sub.title, rows, rows[HEAD][0], rows[-TAIL - 1][0])
            large.append(sheet)
            sheets[i] = sub.model_copy(update={"content": _large_sheet_text(sheet, sub.content, rows)})
    text = "\n\n".join(s.content for s in sheets)
    charts = extracted.text.find("\n\n=== Charts ===\n")
    if charts >= 0:
        text += extracted.text[charts:]
    return f"=== File: {name}{note} ===\n{text}", large


async def render_files(files: dict[str, Path]) -> Rendering:
    """Render files (display name -> path) in name order; raises RecalcError on recalc failure."""
    if not files:
        return Rendering("(no files)")
    extractor = _le.LocalExtractor()
    parts, large = [], []
    for name in sorted(files):
        path = files[name]
        suffix = path.suffix.lower()
        try:
            if suffix in _SHEET_SUFFIXES:
                text, sheets = await _render_workbook(path, extractor, name)
                parts.append(text)
                large += sheets
                continue
            if not extractor.supports_file_type(suffix):
                parts.append(f"=== File: {name} ===\n(not rendered: unsupported file type {suffix or '(none)'})")
                continue
            extracted = await extractor.extract_from_file(path, include_images=False)
            parts.append(f"=== File: {name} ===\n{extracted.text if extracted else ''}")
        except RecalcError:
            raise
        except Exception as exc:  # an unreadable file is evidence, not a crash
            parts.append(f"=== File: {name} ===\n(UNREADABLE: {type(exc).__name__}: {exc})")
    return Rendering("\n\n".join(parts), large)
