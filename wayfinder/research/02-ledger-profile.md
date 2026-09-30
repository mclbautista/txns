# Ledger profile (input for ticket 02)

Source: three uploaded yearly account-transaction exports (2023, 2024, 2025), Xero-style, grouped by expense account. Aggregates only: no vendor, person or client names are reproduced here, and per-row amounts are not listed. Raw CSVs are not committed.

## Shape
- Columns: Date, Source, Description ("Vendor - item text"), Reference, Debit, Credit, Running Balance, Gross, Tax. There is no qty or unit_price column.
- Rows per year (transactions): 2023 = 1,109, 2024 = 954, 2025 = 933. Debits per year: ₱4.71M, ₱4.04M, ₱4.50M. So a run of "at least ₱4M" is about one real year.
- Source mix: Spend Money about 88%, Payable Invoice about 8%, the rest Receive Money, credit notes, depreciation lines.
- About 43% of rows have no item text (vendor only or blank). About 59% of amounts are whole pesos; only about 12% are multiples of 100.
- Median amount about ₱670; percentiles 10/25/50/75/90/99 = ₱89 / ₱175 / ₱670 / ₱2,642 / ₱8,669 / ₱78,750.
- Tax column is 12% VAT on about 13% of rows only (most rows carry no VAT breakdown).
- Weekdays: Mon-Fri about 17% each, Sat 9%, Sun 5%.
- Monthly rows range 37-136; monthly spend ₱115k-₱831k, lumpy (event months Oct-Dec, purchase bursts).
- 57 same-day, same-amount, same-account duplicate groups (batch entry).

## Accounts by three-year spend (top, ₱M): 
Software subscriptions 3.7 (about 25%), computer equipment 1.5, consulting and accounting 1.1, storage devices 0.86, sound equipment 0.85, events/parties 0.51, conference travel 0.5, office meals 0.48, misc 0.42, TV monitors 0.30, marketing 0.30.
Long tail of small accounts: pantry, supplies, fuel, delivery fees (about 50-60 rows per year, ₱40-₱900 each), transport, cleaning, training, equipment rental.

## Recurrence and price observations
- Software subscriptions: about 200 rows per year; a few price points repeat exactly (one ₱2,642 amount 43 times, one ₱26,796 amount 22 times, one ₱349 amount 19 times); charges cluster on the 1st-2nd, 11th, 25th and 29th of the month.
- Storage drives: bought in bursts (1-15 pcs), price stated in text like "15pcs 1TB", "4TB", "12TB"; per-unit price falls out of total / pcs.
- Batch-dated reimbursements: delivery fees logged in bunches on one later date with the original dates in the text.
- Events: deposit then balance pattern (venue deposit, later remaining balance), prizes and raffle items, clustered in Oct-Dec.
