# Circuit Vault — User Manual (v2)

Copy-paste guide for a **new Mac** (or a fresh install). Works with classic Logisim and Logisim Evolution.

---

## What this does

- Your live workspace is the `.circ` file.
- Per-circuit “finals” live in a `circuit-vault/` folder next to that file.
- GitHub is the versioned backup: mark / restore / import / build / undo can **auto commit + push**.
- Restore splices **one** circuit back in; other circuits stay untouched.
- Access tokens go in the **macOS Keychain**, not a plaintext file.

---

## 0. Prerequisites (other Mac)

1. macOS with Terminal
2. **Python 3.11+** (Homebrew recommended)
3. **Git**
4. A **GitHub** account + empty (or existing) lab repo
5. A **Personal Access Token** (classic: `repo` scope, or fine-grained with Contents read/write)

```bash
python3 --version    # need 3.11+
git --version
```

If Python is missing:

```bash
brew install python@3.11
```

---

## 1. Get Circuit Vault onto this Mac

### Option A — clone the tool repo

```bash
cd ~/code_playground   # or any folder you like
git clone https://github.com/YOU/circuit-vault.git
cd circuit-vault
```

### Option B — copy the project folder

Copy the whole `circuit-vault` project directory to this Mac (USB, AirDrop, etc.), then:

```bash
cd /path/to/circuit-vault
```

### Install

Prefer the Python that ships with Homebrew 3.11 if `python3` is 3.14+ and packages fail:

```bash
cd /path/to/circuit-vault
python3 -m pip install -e ".[dev]"
```

Or explicitly:

```bash
/opt/homebrew/opt/python@3.11/bin/python3.11 -m pip install -e ".[dev]"
```

Check it works:

```bash
circuit-vault --help
circuit-vault gui   # should open a window; quit with Cmd+Q or close the window
```

If `circuit-vault: command not found`, ensure your pip scripts path is on `PATH`, or run:

```bash
python3 -m circuit_vault.cli --help
```

---

## 2. Get your lab `.circ` onto this Mac

Circuit Vault protects a **project folder** that contains your `.circ`. That folder is what GitHub backs up.

### If you already synced from the first Mac

Clone the **lab** repo (the one you linked in Setup — not necessarily the tool repo):

```bash
cd ~/Documents   # or wherever you keep coursework
git clone https://github.com/YOU/YOUR-LAB-REPO.git
cd YOUR-LAB-REPO
ls *.circ
ls circuit-vault/    # finals from the other Mac, if you had marked any
```

### If this is the first time

Put your `.circ` in a dedicated folder (recommended):

```bash
mkdir -p ~/Documents/logisim-lab
# copy or move your file there, e.g. main.circ
```

---

## 3. First launch on this Mac (GUI)

```bash
circuit-vault gui
```

### Setup wizard (once per Mac)

1. Paste **GitHub lab repo URL**  
   Example: `https://github.com/YOU/YOUR-LAB-REPO.git`
2. Optional: name + email (used in git commits)
3. Paste **access token** (stored in Keychain on *this* Mac)
4. **Choose .circ to protect first…** → pick your file  
   Example: `~/Documents/logisim-lab/main.circ`
5. Click **OK** — it does a test push

You can **Cancel** the wizard to try features offline; link GitHub later under **Settings**.

### After setup

Status bar should show something like **☁ Synced** when network + token are good.

---

## 4. GUI — full walkthrough

Sidebar tabs: **My File** · **Import** · **Build** · **History** · **Settings**

### Health dots

| Color | Meaning |
|-------|---------|
| Grey | No final saved yet |
| Green | Matches saved final |
| Yellow | Changed vs final |
| Red | Broken — use **Restore** |

Dots refresh about every 5 seconds on **My File**.

---

### Tab: My File

1. **Open .circ** (or use the file from setup)
2. Circuit list appears with dots
3. **Mark Final** on a working circuit → saves XML under `circuit-vault/` and syncs if auto-sync is on
4. If a circuit is yellow/red → **Restore** → confirm → only that circuit is spliced back; a `.bak` is created

**Cross-check:** other circuits in the same file should still look the same after restore.

---

### Tab: Import

1. **Browse shared .circ** (classmate file, another project, etc.)
2. Check circuits to bring in (disabled rows = unfixable)
3. **Merge into** = your active project
4. Clash policy: `replace` | `keep_both` | `skip`
5. **Fix & Merge Selected**
6. If asked about a dependency (e.g. parent needs child), usually choose **Yes**

Back on **My File**, imported circuits should appear.

---

### Tab: Build

1. Describe the circuit (e.g. `4-bit ripple carry adder`)
2. Check allowed components; **Add** custom names if needed
3. Fill Inputs / Outputs
4. **Generate Prompt** → **📋 Copy** → optionally **Open Claude ↗**
5. Paste Claude’s `<circuit>…</circuit>` XML, or **Attach .xml**
6. Preview line should show name + pin/part counts
7. **Build & Merge** into your `.circ`

Open the file in Logisim afterward and **check wiring** — generated circuits can need a manual pass.

---

### Tab: History

1. Recent plain-language actions (also on GitHub when linked)
2. **Undo last action** reverses the latest change (uses backup when available)

---

### Tab: Settings

| Control | What it does |
|---------|----------------|
| GitHub repo / name / email / new token | Re-link or rotate token → **Save & test push** |
| Auto-sync | On = push after every change |
| Push backups (`.bak`) | On = `.bak` files go to GitHub (repo grows over time) |
| Change active `.circ` | Switch which file you protect |

On a new Mac you **must** enter the token again (Keychain does not travel with the repo).

---

## 5. Day-to-day on two Macs

1. On Mac A: work → Mark Final / Import / Build as usual (auto-sync pushes).
2. On Mac B: in the lab folder:

```bash
cd /path/to/YOUR-LAB-REPO
git pull
circuit-vault gui
```

3. Open the same `.circ` (path can differ; content should match after pull).
4. Mark / restore as usual; changes push back to GitHub.
5. Before switching machines again: wait for **☁ Synced**, then on the other Mac `git pull`.

**Do not** edit the same circuit on both Macs offline without pulling — you will get git conflicts in the lab folder.

---

## 6. CLI (same features as GUI)

```bash
# Open + remember this project
circuit-vault open /FULL/PATH/TO/file.circ

# Link GitHub (once per Mac)
circuit-vault setup \
  --repo https://github.com/YOU/YOUR-LAB-REPO.git \
  --name "Your Name" \
  --email "you@school.edu" \
  --token YOUR_PAT

circuit-vault status
circuit-vault mark "Half Adder"
circuit-vault restore "Full Adder 32-bit"
circuit-vault undo

circuit-vault import /path/shared.circ --into /FULL/PATH/TO/file.circ \
  --select "HealthyOR,Parent" --on-clash replace

circuit-vault build-prompt "4-bit adder" \
  --components "AND Gate,OR Gate,my_gate" \
  --inputs "A,B" --outputs "Sum"

circuit-vault build-merge /path/generated.xml --into /FULL/PATH/TO/file.circ

circuit-vault sync    # rare — only if auto-sync was off or push failed
circuit-vault gui
```

Clash options: `replace` | `keep_both` | `skip`

---

## 7. Where files live

Next to your `.circ`:

```text
your-lab-folder/
  main.circ                 ← live Logisim file
  main.circ.bak-…           ← backups from restore / merges
  circuit-vault/            ← canonical finals (*.xml)
  .git/                     ← GitHub backup of this folder
```

App session (last file, repo URL, toggles) on each Mac:

```text
~/.config/circuit-vault/config.json
```

Token: **macOS Keychain** (service name used by Circuit Vault).

---

## 8. Optional: local GUI practice (no GitHub)

From the tool repo (if you have fixtures):

```bash
cd /path/to/circuit-vault
mkdir -p gui-sandbox
cp tests/fixtures/main.circ tests/fixtures/shared_incoming.circ gui-sandbox/
circuit-vault gui
```

1. Cancel setup or turn off **Auto-sync** in Settings.
2. Open `gui-sandbox/main.circ`.
3. Mark Final → Import `shared_incoming.circ` → History Undo.

---

## 9. Troubleshooting

| Problem | Fix |
|---------|-----|
| `No module named 'circuit_vault'` | Re-run `pip install -e ".[dev]"` with the **same** Python as the `circuit-vault` script (`head -1 $(which circuit-vault)`) |
| `command not found: circuit-vault` | Use full path to scripts, or `python3 -m pip install -e .` then restart Terminal |
| Setup / push fails | Check repo URL, token scopes, internet; Settings → **Save & test push**; status bar **Retry sync** |
| Grey dots forever | You never Mark Final’d that circuit |
| Restore does nothing useful | Mark Final while the circuit is healthy **before** it breaks |
| Other Mac missing finals | `git pull` in the lab folder; confirm `circuit-vault/` exists on GitHub |
| GUI won’t open | Ensure PySide6 installed for that Python: `python3 -c "import PySide6"` |

---

## Quick block (new Mac)

```bash
# 1) Install the tool
cd /path/to/circuit-vault
python3 -m pip install -e ".[dev]"

# 2) Get lab files
cd ~/Documents
git clone https://github.com/YOU/YOUR-LAB-REPO.git
cd YOUR-LAB-REPO

# 3) GUI: link GitHub + choose .circ (once on this Mac)
circuit-vault gui

# Or CLI equivalent:
circuit-vault open /FULL/PATH/TO/file.circ
circuit-vault setup --repo https://github.com/YOU/YOUR-LAB-REPO.git --token YOUR_PAT
circuit-vault status
circuit-vault mark "CIRCUIT_NAME"
```

After that, daily use is mostly: open GUI → My File → Mark Final / Restore, and `git pull` when you switch Macs.
