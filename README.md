# HustleVault AI ⚡

> **A local-first, zero-cloud financial intelligence & ledger copilot tailored for freelancers, indie hackers, and side-hustlers.**

Built for the **Hacktoberfest Open-Source AI Challenge: Side Hustle**.  
Eliminates recurring SaaS accounting fees (QuickBooks, Expensify) and safeguards confidential client billing data by running deterministic accounting extraction locally using open-weight models (Ollama: Qwen 2.5 / Llama 3.2).

---

## ✨ Features

- 🧠 **Natural Language Intake:** Paste chaotic invoices, bank SMS alerts, or memo notes (e.g., *"Paid $45 to Vercel for Pro hosting of client app"*).
- 🔒 **Zero-Cloud Privacy:** 100% on-device inference via local Ollama. No third-party LLM API keys. No data leaks.
- 📐 **Deterministic JSON Schema Extraction:** Guarantees strict parsing of Type, Counterparty, Normalized Amount, Currency, Category, Tax-Deductibility status, and CPA justification.
- 📊 **Real-time Financial Metrics:** Live KPI dashboard tracking Gross Income, Operating Expenses, Net Freelance Profit, and Identified Tax Write-Offs.
- 🗄️ **ACID SQLite Persistence:** Lightweight, zero-config local storage (`hustlevault.db`).
- 📥 **One-Click Tax CSV Export:** Instantly export formatted ledgers ready for Schedule C tax filing or your CPA.
- 🛡️ **Offline Resilient:** Graceful fallback extraction engine ensures continuous operation even if the local LLM daemon is offline.

---

## 🏗️ Architecture

```
User (Browser UI)
    │
    ▼
FastAPI Backend (app.py) ──► SQLite (hustlevault.db)
    │
    ▼ (JSON Schema / Deterministic Extraction)
Local Ollama Daemon (http://localhost:11434)
    └── Model: qwen2.5:latest / llama3.2
```

---

## 🚀 Quickstart

### 1. Prerequisites
- Python 3.9+
- [Ollama](https://ollama.ai) installed & running

### 2. Installation
```bash
git clone https://github.com/fokrulanthro16-eng/hustlevault-ai.git
cd hustlevault-ai
pip install -r requirements.txt
```

### 3. Pull Open-Weight Model
```bash
ollama pull qwen2.5:latest
```

### 4. Run HustleVault AI
```bash
python app.py
```
Open **`http://localhost:8000`** in your browser.

### 5. Run Verification Smoke Tests
```bash
python smoke_test.py
```

---

## 📄 License
MIT License. Free and Open Source for the global hacker community.
