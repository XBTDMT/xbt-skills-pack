# G-1 · How to persist the inventory
roadmap: R-2
status: awaiting the user
date: 2026-09-14 · depth: L

goal:      the inventory saved by one run loads back exactly in the next, 0 and None kept apart
gates:     keeps 0 and None apart; stdlib only; atomic writes
options:   | option | how it works | best case | cons | shortcut test |
           | JSON file | save/load a versioned JSON file, written to a temp file then renamed | readable, stdlib | whole-file rewrites | passes |
           | SQLite | one table | concurrent writers | heavier than the library | passes |
           | CSV | one row per item | spreadsheet-friendly | cannot tell 0 from empty without a convention | passes, fragile |
chose:     recommended, awaiting the user: JSON file
rejected:  CSV (0 versus None needs a convention nobody enforces)
evidence:  argued, not tested
revisit:   if two programs must write at once
