# 1. LLM at authoring time only

Status: accepted (ticket 03)

`author` may call an LLM; `generate` never does and needs no network. Same seed, bundle and config give byte-identical CSVs on one machine. Rules are declarative data in the bundle, so a correction is a new bundle version, never a code change.
