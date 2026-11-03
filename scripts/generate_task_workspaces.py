"""Regenerate the binary workspace fixtures (xlsx/pdf) for the example tasks.

Run from the repo root:  uv run python scripts/generate_task_workspaces.py
The generated numbers are consistent with each task's gold_answer.json.
"""

import sys
from pathlib import Path

import openpyxl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "native_tools" / "pdfs" / "tests"))
from pdf_helper import minimal_pdf  # noqa: E402  (single source for minimal PDFs)


def write_xlsx(path: Path, sheets: dict[str, list[list]]) -> None:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    print(f"wrote {path.relative_to(REPO)}")


def q001() -> None:
    ws = REPO / "examples" / "q001_adjusting_entry" / "workspace"
    write_xlsx(
        ws / "ledger.xlsx",
        {
            "TrialBalance": [
                ["Trial Balance — 2025-12-31 (before adjustments)", None, None, None],
                ["Account No", "Account", "Debit", "Credit"],
                [1000, "Cash", 12500.00, None],
                [1100, "Accounts Receivable", 4800.00, None],
                [1400, "Prepaid Insurance", 3600.00, None],
                [1500, "Equipment", 15000.00, None],
                [2000, "Accounts Payable", None, 3200.00],
                [3000, "Share Capital", None, 20000.00],
                [3100, "Retained Earnings", None, 5000.00],
                [4000, "Revenue", None, 18700.00],
                [5000, "Salaries Expense", 9500.00, None],
                [5100, "Rent Expense", 1500.00, None],
                [None, "TOTAL", 46900.00, 46900.00],
            ]
        },
    )
    invoice = minimal_pdf(
        [
            "ACME INSURANCE CO.",
            "INVOICE  INV-2025-0917",
            "Invoice date: 2025-09-28",
            "",
            "Bill to: Teraverse Trading Ltd.",
            "Policy: Commercial property insurance, policy no. CP-88121",
            "Coverage period: 2025-10-01 to 2026-09-30 (12 months)",
            "",
            "Annual premium: 3,600.00",
            "Total due: 3,600.00",
            "",
            "Payment received in full on 2025-10-01. Thank you.",
        ]
    )
    (ws / "invoice.pdf").write_bytes(invoice)
    print(f"wrote {(ws / 'invoice.pdf').relative_to(REPO)}")


def q002() -> None:
    ws = REPO / "examples" / "q002_bank_reconciliation" / "workspace"
    write_xlsx(
        ws / "bank_statement.xlsx",
        {
            "October2025": [
                ["First Pacific Bank — Statement, account 022-889100, October 2025"],
                ["Date", "Ref", "Description", "Amount", "Balance"],
                ["2025-10-01", None, "Opening balance", None, 15000.00],
                ["2025-10-03", "DEP-1003", "Deposit", 3000.00, 18000.00],
                ["2025-10-08", "CHK-2038", "Check 2038", -1200.00, 16800.00],
                ["2025-10-12", "DEP-1012", "Deposit", 2500.00, 19300.00],
                ["2025-10-18", "CHK-2040", "Check 2040", -815.25, 18484.75],
                ["2025-10-25", "CHK-2042", "Check 2042", -249.00, 18235.75],
                ["2025-10-30", "INT-OCT", "Interest earned", 40.00, 18275.75],
                ["2025-10-31", "FEE-OCT", "Monthly service fee", -25.00, 18250.75],
                ["2025-10-31", None, "Closing balance", None, 18250.75],
            ]
        },
    )
    write_xlsx(
        ws / "cash_ledger.xlsx",
        {
            "CashBook": [
                ["Cash ledger (book), October 2025"],
                ["Date", "Ref", "Description", "Amount", "Balance"],
                ["2025-10-01", None, "Opening balance", None, 15000.00],
                ["2025-10-03", "DEP-1003", "Customer deposit", 3000.00, 18000.00],
                ["2025-10-05", "CHK-2038", "Office supplies", -1200.00, 16800.00],
                ["2025-10-12", "DEP-1012", "Customer deposit", 2500.00, 19300.00],
                ["2025-10-15", "CHK-2040", "Utilities", -815.25, 18484.75],
                ["2025-10-20", "CHK-2041", "Contractor payment", -1150.50, 17334.25],
                ["2025-10-22", "CHK-2042", "Software subscription", -249.00, 17085.25],
                ["2025-10-28", "CHK-2043", "Courier services", -320.00, 16765.25],
                ["2025-10-31", "DEP-1031", "Customer deposit", 2400.00, 19165.25],
            ]
        },
    )


if __name__ == "__main__":
    q001()
    q002()
