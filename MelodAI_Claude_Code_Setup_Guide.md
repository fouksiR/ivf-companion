# Melod·AI — Claude Code Setup Guide

**For Dr Yuval Fouks | March 2026**

This guide gets Claude Code running on your machine with full access to your GitHub repo, Google Cloud Run, and Firebase — so you can say things like *"add authentication to all clinician endpoints, test it, deploy to Cloud Run"* and Claude does the whole chain.

---

## Prerequisites Checklist

Before starting, confirm you have:

- [ ] A paid Claude plan (Pro or Max) — Claude Code is included
- [ ] A laptop/desktop (Mac or Windows)
- [ ] Your GitHub account (`fouksiR`) with access to `ivf-companion` repo
- [ ] Your GCP project ID: `fertility-gp-portal`
- [ ] Your Anthropic API key (you'll need a fresh one — the old one was exposed)

---

## STEP 1: Install Claude Code

### Option A: Via Claude Desktop App (Recommended for you)

1. Download Claude Desktop from **https://claude.com/download** (Mac or Windows)
2. Open the app → you'll see three tabs at the top: **Chat | Cowork | Code**
3. Click the **Code** tab — that's Claude Code with a visual interface, no terminal needed to launch it

### Option B: Via Terminal (if you prefer)

```bash
# Requires Node.js 18+
# Check if you have it:
node --version

# If not installed, get it from https://nodejs.org/

# Install Claude Code globally
npm install -g @anthropic-ai/claude-code
```

Then to launch, open a terminal and type:
```bash
claude
```

---

## STEP 2: Install & Authenticate Google Cloud CLI

Claude Code needs `gcloud` on your machine to deploy to Cloud Run.

### Install gcloud CLI

**Mac:**
```bash
# Download and install
curl https://sdk.cloud.google.com | bash

# Restart your terminal, then initialize
gcloud init
```

**Windows:**
Download the installer from: https://cloud.google.com/sdk/docs/install

### Authenticate and set project

```bash
# Login to Google Cloud (opens browser)
gcloud auth login

# Set your project
gcloud config set project fertility-gp-portal

# Set your default region
gcloud config set run/region australia-southeast1

# Verify it works
gcloud run services list
# You should see "ivf-companion" in the list
```

---

## STEP 3: Install & Authenticate Git + GitHub

### Install Git (if not already installed)

```bash
# Check if installed
git --version

# Mac (via Homebrew):
brew install git

# Windows: download from https://git-scm.com/
```

### Authenticate with GitHub

```bash
# Install GitHub CLI (makes auth easy)
# Mac:
brew install gh

# Windows:
winget install --id GitHub.cli

# Login (opens browser):
gh auth login
# Choose: GitHub.com → HTTPS → Login with browser
```

### Clone your repository

```bash
# Navigate to where you want the project
cd ~/Projects  # or wherever you prefer

# Clone
git clone https://github.com/fouksiR/ivf-companion.git

# Enter the directory
cd ivf-companion
```

---

## STEP 4: Set Up Python Environment

Your backend needs Python dependencies for local testing.

```bash
# Check Python version (need 3.9+)
python3 --version

# Create a virtual environment
python3 -m venv venv

# Activate it
# Mac/Linux:
source venv/bin/activate
# Windows:
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

---

## STEP 5: Set Up Environment Variables

Create a `.env` file in your project root (Claude Code will use these):

```bash
# In the ivf-companion directory, create .env
cat > .env << 'EOF'
# Anthropic API Key (GET A NEW ONE — old one was exposed!)
# Go to: https://console.anthropic.com/settings/keys
ANTHROPIC_API_KEY=sk-ant-REPLACE_WITH_NEW_KEY

# Firebase
FIREBASE_URL=https://fertility-gp-portal-default-rtdb.asia-southeast1.firebasedatabase.app

# Fertool Backend
FERTOOL_URL=https://fertility-gp-backend-532857641879.australia-southeast2.run.app

# GCP Project
GCP_PROJECT=fertility-gp-portal
GCP_REGION=australia-southeast1
EOF
```

**CRITICAL: Add `.env` to `.gitignore` so it never gets pushed to GitHub:**

```bash
echo ".env" >> .gitignore
echo "venv/" >> .gitignore
```

---

## STEP 6: Set Up Firebase Credentials Locally

For Claude Code to test Firebase-related code locally:

```bash
# Download your Firebase service account key
# Go to: https://console.firebase.google.com/project/fertility-gp-portal/settings/serviceaccounts/adminsdk
# Click "Generate new private key"
# Save the file as: firebase-service-account.json in your project root

# Add it to .gitignore (NEVER commit this file)
echo "firebase-service-account.json" >> .gitignore

# Set the environment variable
export GOOGLE_APPLICATION_CREDENTIALS="./firebase-service-account.json"
```

---

## STEP 7: Install Claude Code Plugins (Optional but Powerful)

### Firebase Plugin

If using Claude Code from terminal:

```bash
claude plugins add firebase@claude-plugins-official
```

This gives Claude Code direct access to query your Firebase Realtime DB, manage auth, and read/write data — all from within the coding session.

### GitHub Plugin

Claude Code already has built-in Git support, but you can enhance it:

```bash
# Make sure gh CLI is installed and authenticated (Step 3)
# Claude Code will automatically detect and use it
```

---

## STEP 8: Create CLAUDE.md (Project Context File)

This is the magic file. Claude Code reads `CLAUDE.md` from your project root every time you start a session. It gives Claude full context about Melod-AI without you having to explain anything.

**Copy the CLAUDE.md file (provided separately) into your `ivf-companion/` directory.**

```bash
# The CLAUDE.md file should be at:
# ~/Projects/ivf-companion/CLAUDE.md
```

---

## STEP 9: Launch Claude Code on Your Project

### From Claude Desktop App:

1. Open Claude Desktop
2. Click the **Code** tab
3. It will ask you to select a folder → navigate to `~/Projects/ivf-companion/`
4. Claude Code now has full context from CLAUDE.md
5. Start prompting!

### From Terminal:

```bash
cd ~/Projects/ivf-companion
claude
```

### From VS Code (recommended for seeing file changes):

1. Open VS Code
2. Open the `ivf-companion` folder
3. Open the integrated terminal (Ctrl+` or Cmd+`)
4. Type `claude`
5. Now you can see Claude's edits in the editor AND talk to it in the terminal

---

## STEP 10: Verify Everything Works

Once Claude Code is running, test with these prompts:

**Test 1 — Git access:**
```
Show me the current git status and last 3 commits
```

**Test 2 — File access:**
```
Read app.py and tell me how many endpoints are defined
```

**Test 3 — Deploy (dry run):**
```
Show me what the gcloud run deploy command would look like for this project.
Don't actually run it yet.
```

**Test 4 — Full cycle (when ready):**
```
Add a simple health check endpoint at GET /health that returns {"status": "ok"}.
Commit it, push to GitHub, and deploy to Cloud Run.
```

---

## Your Two-Tool Workflow

### On your phone (driving, walking, ideas flowing):

Use **Claude Web/Mobile** (this chat). Dictate ideas, plan features, review architecture.
Keep this conversation going as your "thinking space."

### At your desk (execution time):

Use **Claude Code** in the `ivf-companion` folder. Give it concrete tasks:

- *"Add API key authentication to all /clinician/* endpoints"*
- *"Set up SSE streaming for the /chat endpoint and update index.html to use EventSource"*
- *"Set min-instances=1 on Cloud Run to fix cold starts, deploy it"*
- *"Run the Fertool bridge with a test clinical question and show me the response"*
- *"Clean up the repo — remove dashboard.html, all patch_*.py files, and old spec docs"*

Claude Code will edit the files, run tests, commit, push, and deploy — all from one prompt.

---

## Quick Reference Commands

| What | Command |
|------|---------|
| Start Claude Code (terminal) | `cd ~/Projects/ivf-companion && claude` |
| Deploy backend | `gcloud run deploy ivf-companion --source . --region australia-southeast1 --allow-unauthenticated --memory 2Gi` |
| Deploy frontend | `git add -A && git commit -m "msg" && git push origin main` |
| Read Cloud Run logs | `gcloud run services logs read ivf-companion --region australia-southeast1 --limit 30` |
| Update env var (no rebuild) | `gcloud run services update ivf-companion --region australia-southeast1 --update-env-vars KEY=VALUE` |
| Check Firebase data | `curl https://fertility-gp-portal-default-rtdb.asia-southeast1.firebasedatabase.app/melod_ai/patients.json` |

---

## Troubleshooting

**"gcloud: command not found"**
→ Restart your terminal after installing gcloud CLI. On Mac, run `source ~/.zshrc`.

**"Permission denied" on Cloud Run deploy**
→ Run `gcloud auth login` again, make sure you're using the account that owns the `fertility-gp-portal` project.

**Claude Code can't find files**
→ Make sure you launched it FROM the `ivf-companion` directory, not a parent folder.

**Firebase writes fail locally**
→ Check that `GOOGLE_APPLICATION_CREDENTIALS` points to your service account JSON.
→ Run `echo $GOOGLE_APPLICATION_CREDENTIALS` to verify.

**Git push rejected**
→ Run `git pull origin main --rebase` first to sync any changes made directly in Cloud Shell.
