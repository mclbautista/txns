# Ticket 02: Post-production cost fact sheet

Confidence tags: **[S]** sourced (URL, retrieved 2026-09-30) · **[D-ledger]** derived from Cyril's 2023-2025 expense ledgers (aggregates only, no names) · **[D-calc]** computed from other figures with formula shown · **[U]** unsourced, recall or judgement, verify with Cyril.
Amounts keep their original currency and year. Ledger years are PHP nominal.

## 0. Decisions this sheet is built on
- Spend only: things bought and events; no lines paying a person for their time (labour repairs are allowed).
- Target: about ₱4M and 700-1,000 rows per quarter (about ₱12M a year). Real ledger is about ₱4-4.7M and 930-1,110 rows per **year**, so a quarter is roughly 3x the real row density per period, with a mean row of about ₱4,000-5,700 vs real about ₱4,400 [D-ledger].
- Scale by more rows and larger quantities. Unit prices stay anchored and move only by a yearly uplift.
- Load-test multipliers: per price class (section 6) and per storyline, with a project-tier factor (independent / mid / high-end) on storylines.

## 1. What the real ledger looks like [D-ledger]
| Fact | Value |
|---|---|
| Rows per year | 1,109 (2023), 954 (2024), 933 (2025) |
| Debits per year | ₱4.71M, ₱4.04M, ₱4.50M |
| Row amount p10 / p25 / p50 / p75 / p90 / p99 | ₱89 / ₱175 / ₱670 / ₱2,642 / ₱8,669 / ₱78,750 |
| Source mix | about 88% cash-spend entries, about 8% payable invoices |
| Rows with no item text | about 43% |
| Whole-peso amounts / multiples of ₱100 | about 59% / about 12% |
| Rows carrying VAT breakdown (12%) | about 13% |
| Weekday share | Mon-Fri about 17% each, Sat 9%, Sun 5% |
| Monthly rows / monthly spend | 37-136 rows; ₱115k-₱831k, lumpy |
| Same-day, same-amount, same-account duplicates | 57 groups (batch entry) |

Caveat: the last month of the 2025 export is thin (37 rows, no subscription rows), so it may be incomplete. Treat December 2025 as unreliable.

## 2. Price and quantity facts by category
Percentiles are p10 / p50 / p90 of row amounts in ₱ [D-ledger], year in brackets.

### 2.1 Storage and media bought for clients
| Item | Facts | Tag |
|---|---|---|
| Portable and desktop hard drives | Ledger rows 2023-2025: about 10 rows a year, median ₱15-25k per row, mostly bulk. Bulk pack seen: 15 x 1TB at about ₱1,700 each (2024); 4 x USB sticks; single 4-12TB drives at ₱6,000-₱30,000 per row; SSD 4TB about ₱75,000-₱150,000 per row for high-end. Per-unit price is total divided by pcs. | D-ledger, D-calc |
| PH retail, external HDD 4TB | WD My Passport 4TB ₱5,600; WD My Book 4TB ₱7,225; Seagate One Touch 4TB ₱7,350-₱10,450; WD My Book 8TB ₱11,300; Seagate 8TB hub ₱15,795 | S ([iprice.ph](https://iprice.ph/western-digital/computing/external-storage/hdd/), [bermorzone](https://bermorzone.com.ph/shop/storage-devices/external-storage-drives/western-digital-4tb-6tb-8tb-12tb-14tb-16tb-18tb-22tb-my-book-wd-desktop-external-hard-drive-usb-3-0-external-hdd-portable/), [complink](https://www.complink.com.ph/collections/seagate)) |
| USB flash drives | 64GB about ₱450-₱750, 128GB about ₱495-₱1,500 depending on model | S ([itech.ph](https://www.itech.ph/shop/storage/flash-drives-storage/), [datablitz](https://ecommerce.datablitz.com.ph/collections/flash-drive)) |
| LTO-8 / LTO-9 cartridge | US list: LTO-8 about $70-$80, LTO-9 about $92-$112 (some sellers higher). At the 2025 BSP average of ₱57.51 per US$ that is about ₱4,000-₱4,600 (LTO-8) and ₱5,300-₱6,400 (LTO-9) before shipping, duty and VAT. No Philippine seller price found. Ledger shows **no LTO purchases** | S for US list ([tapebackup.org](https://tapebackup.org/lto-tape-price-trend), [backupworks](https://www.backupworks.com/LTO-9-tape-media.aspx)); PH price D-calc, low confidence |
| LTO buying pattern (packs of 5, 20, retainers) | Not found. Vendors list single and multi-packs (for example 20-pack listings) | U |
| Blu-ray / M-DISC optical | US only: M-DISC BD-R DL 50GB 25-pack about $143-$270; BD-R 25GB 25-pack about $77. No PH price found; ledger has no optical purchases | S US only ([bhphoto](https://www.bhphotovideo.com/c/product/1197976-REG/verbatim_98924_50gb_bd_r_dl_6x.html), [walmart](https://www.walmart.com/c/kp/m-disc-verbatim)); PH U |
| Web / cloud storage | Object storage about $6.95 per TB per month (Backblaze B2), Google Workspace 2TB per user about $12, Dropbox Business Standard $18 per user with 5TB. Ledger shows recurring cloud and storage subscriptions inside the subscription class | S ([backblaze](https://www.backblaze.com/cloud-storage/pricing), [cloudzero](https://www.cloudzero.com/blog/cloud-storage-pricing/)), D-ledger |

### 2.2 Software and web subscriptions (about 25% of spend)
- About 190-245 rows a year, ₱1.16M-₱1.34M a year, so roughly ₱100k per month with a spike in the busy month [D-ledger].
- Row amount p10 / p50 / p90: ₱676 / ₱1,741 / ₱14,648 (2023); ₱459 / ₱2,642 / ₱21,914 (2024); ₱907 / ₱2,891 / ₱26,466 (2025). Median rises about 60% over 2 years [D-ledger].
- Exact price repeats: in 2023 about 25% of rows are amounts repeated 3+ times; 2025 about 16%. The largest repeated amounts recur about 20-40 times over three years [D-ledger].
- Billing days cluster on the 1st-2nd, 11th, 25th and 29th; monthly row count swings between 7 and 33 (seat and tier changes) [D-ledger].
- Monthly totals: ₱55k-₱229k, so a single month can double the baseline [D-ledger].

### 2.3 Computers, sound, screens, furniture
| Class | p10 / p50 / p90 (₱) | Rows a year | Tag |
|---|---|---|---|
| Computer equipment | 3,290 / 25,465 / 120,000 (2023); 40,000 / 75,893 / 196,429 (2025) | 5-12 | D-ledger |
| Computer accessories | 350 / 2,100 / 15,972 (2023); 1,160 / 12,010 / 41,514 (2025) | 6-13 | D-ledger |
| Sound system and gear | 831 / 5,000 / 150,000 (2023); 5,000 / 22,500 / 77,524 (2025) | 4-16 | D-ledger |
| TV and reference monitors | 1,250 / 21,990 / 49,104 (2023); 8,791 / 35,900 / 96,428 (2025) | 1-4 | D-ledger |
| Office furniture | 964 / 4,713 / 32,140 (2023); one row of 12,400 (2025) | 1-18 | D-ledger |
| Network and IT | 234 / 5,000 / 12,500 (2024); 95 / 7,500 / 7,500 (2025, monthly-style repeat of ₱7,500) | 4-28 | D-ledger |

### 2.4 Events, catering, training
- Event pattern: venue deposit then balance weeks later (for example ₱5,000 deposit and ₱37,468 balance; ₱13,000 and ₱18,810 venues), prizes in stepped tiers (₱5,000 / ₱3,000 / ₱2,000), raffle and game prizes, photobooth about ₱3,500, printed shirts about ₱12,000 for a batch, plus a large cash fund of ₱50,000 [D-ledger].
- Events cluster in Oct-Dec (a Christmas party month can add 20-30 small rows on one day) and one mid-year event [D-ledger].
- Event row p10 / p50 / p90: ₱200 / ₱2,000 / ₱25,000 (2023); ₱599 / ₱1,200 / ₱37,468 (2025) [D-ledger].
- Training and seminars: one or two rows a year, ₱2,800 (safety seminar) to ₱20,000 (a 4-session course) [D-ledger].
- Conference travel (festival trip): visa fee about ₱15,500, travel fund ₱125,000, lodging about ₱224,000 in one year, none in 2025 [D-ledger].

### 2.5 Office and petty spend
| Class | p10 / p50 / p90 (₱) | Rows a year | Tag |
|---|---|---|---|
| Office meals | 98 / 360 / 1,741 (2023); 235 / 780 / 3,150 (2025) | 104-277 | D-ledger |
| Pantry supplies | 132 / 393 / 1,089 (2023); 105 / 187 / 828 (2025) | 50-156 | D-ledger |
| Office supplies | 67 / 411 / 1,244 (2023); 177 / 483 / 2,110 (2025) | 58-87 | D-ledger |
| Fuel | 89 / 134 / 268 (2023); 215 / 245 / 268 (2025) | 61-92 | D-ledger |
| Delivery fees (courier of client drives) | 49 / 95 / 253 (2023); 89 / 192 / 447 (2025) | 31-62 | D-ledger |
| Repairs, labour | 150 / 7,500 / 15,000 (2023); 2,000 / 10,500 / 33,000 (2025) | 7-12 | D-ledger |
| Marketing | 500 / 2,940 / 20,000 (2023); 240 / 720 / 2,194 (2025) | 12-22 | D-ledger |
| Cleaning and pest control | 100 / 882 / 13,500 (2024); 1,748 / 2,498 / 6,954 (2025) | 3 | D-ledger |

Delivery fees rise about 2x between 2023 and 2025 (median ₱95 to ₱192) and are often logged in batches on one later date with the original dates in the text [D-ledger].

## 3. How things are bought
- Cash-spend entries dominate (about 88%); invoices are used for bigger or repeat vendors [D-ledger].
- Bulk buys of drives come in packs of 1, 4 and 15; single drives are more common for high capacity [D-ledger].
- Monthly retainer-like lines: network line of ₱7,500 repeating, some subscriptions on fixed billing days [D-ledger].
- Batch entry: several identical rows on one date (57 duplicate groups) and delivery reimbursements logged after the fact [D-ledger].
- LTO and optical bulk pack sizes and retainers: not found [U].
- Stage or facility hire minimums: not found beyond one theatre rental of ₱90,000 and a DCP preview of ₱7,000 (2025) [D-ledger, single observations].

## 4. Price change over a year
- Rate cards change rarely; the ledger shows step changes at about yearly intervals. Subscription median grew about 60% from 2023 to 2025; delivery median about doubled [D-ledger].
- FX: BSP annual average ₱55.63 (2023), ₱57.29 (2024), ₱57.51 (2025) per US$, about 3.4% depreciation over two years [S] ([BSP](https://www.bsp.gov.ph/statistics/external/tab12_pus_data.aspx)). Relevant for USD-priced subscriptions and imported media.
- Typical uplift cadence (annual, sometimes mid-year) is an inference, not sourced [U].

## 5. Tax
- VAT is 12% on sale of goods and services [S] ([deskera](https://www.deskera.com/blog/philippines-vat-bir/)).
- Expanded withholding: 1% on goods and 2% on services, computed on the amount excluding VAT; applies to regular suppliers or a single purchase of ₱10,000 or more [S] ([cloudcfo](https://cloudcfo.ph/resources/ph-taxes/withholding-tax), [respicio](https://www.respicio.ph/commentaries/basic-rules-on-vat-and-expanded-withholding-tax-in-the-philippines)).
- In the ledger, VAT appears on only about 13% of rows, always exactly 12% of the net; the recorded amount is the net figure, with gross shown separately [D-ledger]. So `unit_price` in the output can be treated as the amount paid, with VAT rarely broken out.

## 6. Price classes and how each can scale (for load-test multipliers)
| Class | Behaviour in the ledger | How it can scale |
|---|---|---|
| Recurring subscriptions | Exact repeats on fixed days; row count and total swing month to month with seats and tiers | Multiply seat count or tier; allow large month-to-month seat swings (7 to 33 rows a month observed) |
| Retail purchases | Small amounts, odd pesos, few-percent spread across sellers, about 59% whole pesos | Multiply quantity and number of orders, not unit price |
| Big-ticket quotes | Rare, round or negotiated numbers (₱12,000, ₱90,000, ₱125,000), one-off | Multiply scope (more units, bigger venue), not the per-unit price |

Storylines (delivery season, archive migration, festival trip, parties, studio upkeep, training, subscription stack, petty daily spend) get an additional tier factor for independent, mid-tier and high-end studios. The size of that factor is not sourced [U].

## 7. Storyline notes (expanded catalog, all prices below marked as noted)
| Storyline | In ledger [D-ledger] | Additions to price later [U] unless noted |
|---|---|---|
| Delivery season | Bulk drive buys, USB sticks, courier fees in batches | Carry cases, labels, packing materials |
| Archive migration | Large-capacity drives labelled for archiving (8TB, 12TB) | LTO cartridges (US list in 2.1), cleaning cartridges, barcode labels, tape cases, docks, NAS/RAID enclosures, shelving, humidity loggers |
| Festival trip | Travel fund, visa fee, lodging, calling cards | Flights, print collateral |
| Parties | Venue deposit and balance, prizes, photobooth, shirts, cash fund | Catering by head count |
| Studio upkeep | Labour repairs, cleaning, pest control, network, furniture | Aircon servicing, UPS batteries, cables, acoustic foam, generator fuel, extinguisher refills |
| Training | One-off seminars | Catered workshops |
| Subscription stack | Recurring on fixed days | Domain, VPN |
| Petty daily spend | Pantry, supplies, fuel, meals, transport | Printing |

## 8. Verify with Cyril [U]
1. Philippine price and pack sizes for LTO cartridges, cleaning tapes and optical media (none seen in the ledger).
2. Whether the December 2025 export is complete.
3. Size of the independent / mid-tier / high-end tier factors.
4. How often vendors change price lists (ledger only shows the outcome).
5. Any real quotes for stage or facility hire and catering per head.

## 9. Method and limits
Ledger figures are computed from three exported account-transaction files with vendor and person names removed; only aggregates and rounded example points are shown, and the raw files are stored in `inputs/ledgers/` in this private repo (ADR 0005). Web figures were retrieved 2026-09-30; US-only list prices are converted only in section 2.1, with the arithmetic shown. Everything not tagged S or D is an unsourced judgement.
