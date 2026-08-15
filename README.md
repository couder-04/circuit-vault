# Circuit Vault

Protect Logisim / Logisim Evolution `.circ` files: save per-circuit **finals**, restore one circuit without touching the rest, import shared work, and build from a Claude prompt. GitHub backs up your project folder automatically.

Most people only need the **GUI**. Follow the steps below in order if this is your first time.

---

## Before you start (first time ever)

You need three things from GitHub: an **account**, an empty **repo**, and a **PAT** (password-like token). Do this in a browser before opening Circuit Vault.

### A. Create a GitHub account (skip if you have one)

1. Open [https://github.com/signup](https://github.com/signup)
2. Sign up with email / password and verify your email

### B. Create an empty repository (your lab backup)

1. Log in → click the **+** (top right) → **New repository**
2. **Repository name**: something simple, e.g. `logisim-lab`
3. Leave it **Public** or **Private** (either works)
4. **Do not** check “Add a README”, “Add .gitignore”, or “Choose a license”  
   (empty repo is easiest for first sync)
5. Click **Create repository**
6. Copy the HTTPS URL shown on the next page. It looks like:

   `https://github.com/YOUR_USERNAME/logisim-lab.git`

   Keep this — you will paste it into Circuit Vault.

### C. Create a Personal Access Token (PAT)

GitHub no longer lets apps use your normal password. A PAT is a special one-time password.

**Easiest path — classic token:**

1. Open [https://github.com/settings/tokens](https://github.com/settings/tokens)  
   (GitHub → your profile picture → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**)
2. Click **Generate new token** → **Generate new token (classic)**
3. **Note**: e.g. `circuit-vault mac`
4. **Expiration**: pick a date you are comfortable with (e.g. 90 days)
5. Under **scopes**, check **`repo`** (full control of private repositories)  
   That is enough for Circuit Vault to push your lab files.
6. Click **Generate token**
7. **Copy the token immediately** (starts with `ghp_…`).  
   GitHub shows it **once**. Paste it into a notes app temporarily until setup finishes.  
   Do **not** share it or commit it to a file.

(Fine-grained tokens also work if you grant **Contents: Read and write** on that one repo.)

---

## Install Circuit Vault (once per Mac)

1. Install **Python 3.11+** and **Git** if needed (`brew install python@3.11 git`)
2. In Terminal:

```bash
cd /path/to/circuit-vault
python3 -m pip install -e ".[dev]"
circuit-vault --help
```

If that prints help text, you are ready.

---

## Start the GUI

```bash
circuit-vault gui
```

A window opens. Quit anytime with **Cmd+Q** or by closing the window. Run `circuit-vault gui` again whenever you want to use it.

---

## First launch — fill the setup wizard

When the app asks to **Link GitHub**:

| Field | What to type |
|-------|----------------|
| **GitHub repo URL** | The URL from step B, e.g. `https://github.com/YOUR_USERNAME/logisim-lab.git` |
| **Name** (optional) | Your name (shows on commits) |
| **Email** (optional) | Your email |
| **Access token** | The PAT from step C (`ghp_…`) — saved in **macOS Keychain**, not a plaintext file |

Then:

1. Click **Choose .circ to protect first…**
2. Pick your Logisim file (the one you edit in Logisim)
3. Click **OK**

Circuit Vault does a **test push**. If it works, the bottom bar should show **☁ Synced**.

You can **Cancel** the wizard to try the app offline; link GitHub later under **Settings** (same fields).

**On a new Mac:** install again, create or reuse a PAT, open the GUI, enter repo URL + token once more. Keychain does not copy between Macs.

---

## How to use the GUI

Sidebar: **My File** · **Import** · **Build** · **History** · **Settings**

### Health dots (My File)

| Dot | Meaning | What to do |
|-----|---------|------------|
| Grey | No final yet | **Mark Final** when it works |
| Green | Matches saved final | Nothing — you’re good |
| Yellow | Changed since final | **Mark Final** (keep new) or **Restore** (go back) |
| Red | Broken | **Restore** |

Dots refresh every few seconds on **My File**.

---

### My File — everyday work

1. **Open .circ** if you did not pick one in setup
2. When a circuit is correct → **Mark Final**
3. If something breaks → **Restore** → confirm  
   Only that circuit is replaced; others stay as-is. A `.bak` backup is created.

---

### Import — pull in a shared `.circ`

1. **Browse shared .circ**
2. Check the circuits you want (greyed-out = couldn’t auto-fix)
3. Choose **Merge into** and clash policy: `replace` / `keep_both` / `skip`
4. **Fix & Merge Selected** (say **Yes** if asked about a dependency)

---

### Build — generate with Claude

1. Describe the circuit
2. Check components (add custom names if needed)
3. Inputs / outputs → **Generate Prompt** → **📋 Copy** (optional: **Open Claude ↗**)
4. Paste `<circuit>…</circuit>` XML, or **Attach .xml**
5. **Build & Merge**

Then open the file in Logisim and **check wiring**.

---

### History

- Recent actions (also on GitHub when linked)
- **Undo last action** to reverse the latest change

---

### Settings

- Change repo / token → **Save & test push**
- **Auto-sync** — push after every change (default on)
- **Push backups (.bak)** — include backups on GitHub (repo grows over time)
- **Change active .circ**

---

## Typical first session (checklist)

1. Create GitHub repo + PAT (sections A–C above)
2. `pip install` Circuit Vault
3. `circuit-vault gui` → paste **repo URL** + **token** → choose your `.circ` → OK
4. **My File** → **Mark Final** on each working circuit
5. Keep editing in Logisim; if a circuit breaks → **Restore**
6. Confirm status bar says **☁ Synced** when online

---

## Notes

- Live file = your `.circ`. Finals = `circuit-vault/` next to it. Backups = `*.bak`.
- Never put your PAT in the repo or in chat screenshots.
- If the token expires later: GitHub → new token → **Settings** in the app → paste → **Save & test push**.
- CLI, two-Mac pull/push, troubleshooting → **[USER_MANUAL.md](USER_MANUAL.md)**.
