# Circuit Vault

Your workspace is the live `.circ` file. Circuit Vault stores per-circuit
canonical “finals” under `circuit-vault/`. GitHub is the versioned backup —
every mark, restore, import, and build **auto add/commit/pushes** (including
`.bak` backups by default). Restore splices one circuit without rewriting the rest.

Works with classic Logisim and Logisim Evolution.

## Install

```bash
pip install -e ".[dev]"
```

## First run

`circuit-vault gui` asks once for a GitHub repo URL + access token (token goes in
the OS keychain, not a plaintext file). After that, sync is automatic.

## GUI (5 tabs)

1. **My File** — health dots, Mark Final / Restore  
2. **Import** — open a shared `.circ`, auto-fix what we can, merge selected circuits  
3. **Build** — describe a circuit → copy prompt to Claude → paste/attach XML → merge  
4. **History** — plain-language log + Undo  
5. **Settings** — repo, auto-sync, push `.bak` toggle  

## CLI

```bash
circuit-vault open path/to/main.circ
circuit-vault status
circuit-vault mark "Full Adder"
circuit-vault restore "Full Adder 32-bit"
circuit-vault undo
circuit-vault import shared.circ --into main.circ --select "A,B" --on-clash replace
circuit-vault build-prompt "4-bit adder" --components "AND Gate,OR Gate,my_gate"
circuit-vault build-merge generated.xml --into main.circ
circuit-vault setup --repo https://github.com/you/repo.git --token …
circuit-vault sync
circuit-vault gui
```

## Honest notes

- Build-generated circuits may need a **wiring check in Logisim** after merge.
- Pushing `.bak` files grows the repo over time — turn off in **Settings** if needed.
- No XML normalization, no hash-based detection, no custom versioning beyond Git.
