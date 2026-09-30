# 5. Ledgers stored in the private repo

Status: accepted (Cyril, 2026-09-30); supersedes the "no raw rows committed" line in tickets 02 and 06

Raw ledgers live in `inputs/ledgers/`. `author` derives `reference.json` from them and stores it in the bundle, so `generate` never reads the ledgers and bundle hashes do not change on re-export. The ledgers contain vendor and person names: this repository must stay private.
