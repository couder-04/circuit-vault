# Circuit Vault — Commands (v2)

Copy-paste guide.

---

## 1. Install

```bash
cd /path/to/circuit-vault
pip install -e .
```

```bash
circuit-vault --help
```

---

## 2. GUI

```bash
circuit-vault gui
```

First launch: paste GitHub repo URL + access token (saved in keychain). Then use tabs:

| Tab | What it does |
|-----|----------------|
| My File | Mark Final / Restore (dots update every few seconds) |
| Import | Browse a shared `.circ` → fix → merge selected |
| Build | Describe → Generate Prompt → Copy → Claude → paste XML → Build & Merge |
| History | Recent changes + Undo |
| Settings | Repo, auto-sync, push `.bak` on/off |

---

## 3. Open a file (CLI)

```bash
circuit-vault open /FULL/PATH/TO/file.circ
```

---

## 4. Status / mark / restore / undo

```bash
circuit-vault status
circuit-vault mark "NOT"
circuit-vault restore "NOT"
circuit-vault undo
```

---

## 5. Import a shared file

```bash
circuit-vault import /path/to/shared.circ --into /path/to/yours.circ --select "HealthyOR,Parent" --on-clash replace
```

Clash options: `replace` | `keep_both` | `skip`

---

## 6. Build from Claude

```bash
circuit-vault build-prompt "4-bit adder" --components "AND Gate,OR Gate,my_gate" --inputs "A,B" --outputs "Sum"
```

```bash
circuit-vault build-merge /path/to/generated.xml --into /path/to/yours.circ
```

Generated circuits may need a wiring check in Logisim.

---

## 7. Link GitHub (one time)

```bash
circuit-vault open /FULL/PATH/TO/file.circ
circuit-vault setup --repo https://github.com/YOU/REPO.git --name "Your Name" --email "you@school.edu" --token YOUR_PAT
```

---

## 8. Manual sync (rare)

```bash
circuit-vault sync
```

Every mark / restore / undo / import / build already syncs when auto-sync is on.

---

## 9. Where files live

```bash
cd /folder/with/your.circ
ls circuit-vault/
ls *.bak
```

`.bak` files are pushed to GitHub by default (Settings can turn that off — repos grow over time).

---

## Quick block

```bash
cd /path/to/circuit-vault && pip install -e .
circuit-vault open /FULL/PATH/TO/file.circ
circuit-vault setup --repo https://github.com/YOU/REPO.git --token YOUR_PAT
circuit-vault status
circuit-vault mark "CIRCUIT_NAME"
circuit-vault restore "CIRCUIT_NAME"
circuit-vault import /path/shared.circ --into /FULL/PATH/TO/file.circ --select "A,B"
circuit-vault build-prompt "description" --components "AND Gate,my_gate"
circuit-vault build-merge generated.xml --into /FULL/PATH/TO/file.circ
circuit-vault gui
```
