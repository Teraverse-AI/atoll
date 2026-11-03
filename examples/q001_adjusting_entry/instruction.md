# Year-end adjusting entry: prepaid insurance

The company's fiscal year ends on **2025-12-31**.

The workspace contains:

- `ledger.xlsx` — the general ledger trial balance as of 2025-12-31, **before adjustments**
- `invoice.pdf` — the insurance invoice that was posted to Prepaid Insurance when paid

On **2025-10-01** the company paid a 12-month insurance policy premium in advance and
debited the full amount to **Prepaid Insurance** (account 1400). No adjusting entry
has been recorded since.

## Your task

1. Read the invoice to determine the policy premium and coverage period.
2. Compute the portion of the premium that expired by 2025-12-31.
3. Write the year-end adjusting journal entry to `adjusting_entry.json` in the
   workspace root, in exactly this shape:

```json
{
  "date": "2025-12-31",
  "memo": "...",
  "entries": [
    {"account": "Insurance Expense", "debit": 0.0, "credit": 0.0},
    {"account": "Prepaid Insurance", "debit": 0.0, "credit": 0.0}
  ]
}
```

Debits must equal credits. Round to 2 decimal places. When finished, state the
expired amount and the remaining prepaid balance in your final answer.
