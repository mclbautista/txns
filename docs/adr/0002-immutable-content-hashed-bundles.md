# 2. Immutable content-hashed bundles

Status: accepted (ticket 03)

Bundles live at `bundles/<label>-<hash>/`, are never edited in place, and are `reviewed: false` until `approve`. `generate` uses the latest promoted bundle unless `--bundle` is given and warns on unreviewed bundles. Run identity is seed + bundle hash + config hash.
