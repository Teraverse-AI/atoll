# Bank reconciliation — October 2025

The workspace contains:

- `bank_statement.xlsx` — the bank's statement for October 2025
- `cash_ledger.xlsx` — the company's cash book for the same period

## Your task

Reconcile the bank statement balance to the company's book balance as of
**2025-10-31**. Identify outstanding checks (in the books, not on the statement),
deposits in transit (in the books, not on the statement), and any bank-only items
(fees, interest) not yet booked.

Write `reconciliation.json` to the workspace root:

```json
{
  "statement_balance": 0.0,
  "deposits_in_transit": [{"ref": "...", "amount": 0.0}],
  "outstanding_checks": [{"ref": "...", "amount": 0.0}],
  "adjusted_bank_balance": 0.0,
  "book_balance": 0.0,
  "unbooked_bank_items": [{"ref": "...", "amount": 0.0}],
  "adjusted_book_balance": 0.0
}
```

The two adjusted balances must agree. Round to 2 decimal places. State the adjusted
balance in your final answer.
