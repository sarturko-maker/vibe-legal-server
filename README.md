# Vibe Legal Server
**Batch contract redlining powered by AI**

---

## ⚠️ Please Read Before Using

This is a **vibe coded** project — built by a lawyer who doesn't know how to code, using AI development tools.

It exists to generate ideas, spark conversation, and contribute to the open source legal tech community. It is **not a commercial product**. It is **not production-ready software**.

**No liability is accepted.** Use this at your own risk. Always review AI suggestions carefully. Never rely on this tool for legal advice or critical work.

---

## Known Limitations

This project has **not been extensively tested**. Known issues remain.

**What hasn't been tested:**
- Documents containing **tables** — behaviour is unpredictable
- Complex nested numbering schemes
- Documents with heavy formatting (columns, text boxes, footnotes)
- Non-English documents

**Current architecture limitations:**
- The system relies entirely on **AI for validation** — there is no deterministic code checking whether the AI's output is correct before applying changes
- A malformed AI response can corrupt your document structure
- No rollback mechanism if something goes wrong mid-process

**What this means for you:**
- Always work on **copies** of important documents
- Review every change the AI makes
- Consider this a prototype, not a production tool

Future versions would benefit from deterministic validation layers that verify AI outputs before applying them to documents.

---

## What This Is

Vibe Legal Server is Part 2 of the Vibe Legal project.

**Part 1** was a Word add-in for redlining contracts one at a time, interactively.

**Part 2** (this project) is a batch processing server. Upload documents. Select a playbook. Get back redlined contracts with tracked changes — automatically.

Think of it like this:
- The Word add-in is for **negotiation** — when you're going back and forth on a single contract
- The Server is for **review** — when you have a pile of contracts and want them marked up against your playbook

---

## What It Does

1. **Upload a contract** (or up to 5 at once in batch mode)
2. **Select a playbook** — your negotiation position and priorities
3. **Click process**
4. **Download redlined documents** with proper tracked changes

The AI reads your contract against the playbook, identifies issues, and applies changes surgically — as redlines, not rewrites.

### Example Playbooks

- **Standard NDA Review** — Focus on term length, mutual obligations, carve-outs
- **Sales Agreement (Buyer)** — Push back on payment terms, delivery risk, warranty periods
- **Supplier Contract** — Protect against unlimited liability, ensure IP ownership is clear

You can create your own playbooks in Markdown format.

---

## Features

### Single Document Review
Upload one document, process it against a playbook, download the result.

### Batch Processing
Upload up to 5 documents at once. They process sequentially. Download individually or as a ZIP.

### Playbook System
Define your negotiation position in a Markdown file:
- What to look for
- What changes to suggest
- Which party you represent

### Proper Track Changes
Changes appear as genuine Word tracked changes — strikethroughs and underlines — not comments or rewrites.

### Choose Your AI
Currently supports Google Gemini. Bring your own API key.

---

## How It Works

The server:
1. Extracts structure from your Word document (clauses, sections, numbering)
2. Sends the structure + playbook to the AI
3. Gets back targeted operations (AMEND, INSERT, DELETE)
4. Applies each operation as a tracked change in the document
5. Returns the redlined document for download

The AI doesn't rewrite your contract — it makes surgical changes in specific locations.

---

## Security & Privacy

This is a **"Bring Your Own Key"** tool.

- Your API key is stored in your browser's local storage
- Documents are sent to your chosen AI provider
- Nothing is stored permanently on the server

**What you should know:**
- Documents pass through this server temporarily during processing
- Make sure sending documents to AI providers complies with your firm's policies
- This is research software — always review AI suggestions carefully

---

## Getting Started

### What You'll Need

- Python 3.10+
- Node.js 18+ (for the frontend)
- A Google Gemini API key

### Quick Start

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/VibeLegalPython.git
cd VibeLegalPython

# Set up Python backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt

# Start the backend server
python main.py

# In a new terminal, set up the frontend
cd vibelegal-frontend
npm install
npm run dev
```

Then open localhost in your browser.

### First Time Setup

1. Go to **Settings**
2. Enter your Gemini API key
3. Click **Test Connection**
4. Select a model and **Save**

Now you can:
- Use **Review** for single documents
- Use **Batch** for multiple documents

---

## Project Structure

```
VibeLegalPython/
├── main.py                 # FastAPI server
├── job_processor.py        # AI processing logic
├── Playbooks/              # Your negotiation playbooks
├── ooxml_lib/              # Word document manipulation
│   ├── style_aware_editor.py   # Track changes operation
│   ├── structure_detector.py   # Document parsing
│   └── styler_ai.py            # Formatting fixes
└── vibelegal-frontend/     # React frontend
    └── src/pages/
        ├── Review.tsx      # Single document
        ├── Batch.tsx       # Multiple documents
        └── Playbooks.tsx   # Manage playbooks
```

---

## Creating Playbooks

Playbooks are Markdown files in the `Playbooks/` folder.

Example structure:
```markdown
# SELLER'S PLAYBOOK

## Priority Issues
1. Payment terms — require payment on delivery, not before
2. Liability cap — maintain full purchase price cap
3. Warranty period — keep it short

## Watch For
- Unlimited liability language
- Buyer-friendly termination rights
- IP assignment clauses

## Suggested Language
When negotiating delivery terms, propose: "Delivery shall be FOB destination..."
```

The AI uses your playbook to decide what to flag and how to negotiate.

---

## Reminder

**AI makes mistakes.** It might remove a liability cap while telling you it fixed a typo. It might miss obvious issues. It might hallucinate clauses that don't exist.

Always review every change. You are responsible for the final document.

---

## The Bigger Picture

This is **Part 2** of the Vibe Legal project:

- **Part 1** — Word Add-Inn for interactive negotiation
- **Part 2** — This server for batch processing
- **Part 3** — A vision for what legal workflows could look like (coming soon)

---

## Tech Stack

Built using:
- **FastAPI** — Python web framework
- **python-docx + lxml** — Word document manipulation  
- **React + Vite** — Frontend
- **Google Gemini** — AI provider
- **Tailwind CSS** — Styling

---

## Get In Touch

Questions? Ideas? Found a bug?

Open an issue on GitHub.
