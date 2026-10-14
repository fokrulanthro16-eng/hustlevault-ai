import os
import sys
import json
import re
import csv
import io
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import httpx

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "hustlevault.db"
STATIC_DIR = BASE_DIR / "static"

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:latest")
ESTIMATED_TAX_RATE = 0.25  # Standard freelance self-employment / income tax estimate

# ==========================================
# DATABASE INITIALIZATION & HELPERS
# ==========================================
def get_db():
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('income', 'expense')),
                party TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'USD',
                category TEXT NOT NULL,
                tax_deductible INTEGER NOT NULL DEFAULT 0,
                rationale TEXT,
                raw_memo TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON transactions(date);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_type ON transactions(type);")
        conn.commit()

# ==========================================
# PYDANTIC DATA MODELS
# ==========================================
class ParseRequest(BaseModel):
    memo: str = Field(..., min_length=3, description="Messy transaction memo string")

class ParsedTransaction(BaseModel):
    type: str = Field(..., description="'income' or 'expense'")
    party: str = Field(..., description="Vendor or Client name")
    amount: float = Field(..., description="Numerical amount")
    currency: str = Field("USD", description="Currency ISO code (e.g., USD, EUR)")
    category: str = Field(..., description="Accounting category")
    tax_deductible: bool = Field(False, description="Whether this is a legitimate business tax deduction")
    rationale: str = Field(..., description="Brief CPA rationale for tax and category classification")

class TransactionCreate(BaseModel):
    date: Optional[str] = None
    type: str
    party: str
    amount: float
    currency: str = "USD"
    category: str
    tax_deductible: bool = False
    rationale: Optional[str] = None
    raw_memo: Optional[str] = None

class TransactionOut(BaseModel):
    id: int
    date: str
    type: str
    party: str
    amount: float
    currency: str
    category: str
    tax_deductible: bool
    rationale: Optional[str]
    raw_memo: Optional[str]
    created_at: str

# ==========================================
# FASTAPI APPLICATION
# ==========================================
app = FastAPI(
    title="HustleVault AI",
    description="Local-first, zero-cloud financial intelligence & ledger copilot",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    init_db()

# ==========================================
# SYSTEM PROMPTS & INFERENCE ENGINE
# ==========================================
EXTRACTION_SYSTEM_PROMPT = """You are HustleVault AI, a deterministic, privacy-first CPA & ledger intelligence copilot for freelancers, solopreneurs, and indie hackers.
Extract financial transaction data from the user memo and output ONLY a strictly valid JSON object matching this schema:
{
  "type": "income" or "expense",
  "party": "Name of Vendor or Client",
  "amount": 0.00,
  "currency": "USD" (or EUR, GBP, CAD, etc.),
  "category": "Standard category (e.g., Software & SaaS, Cloud Hosting, Hardware, Freelance Income, Client Retainer, Office Supplies, Advertising, Legal & Professional, Travel & Meals, Education)",
  "tax_deductible": true or false (true if ordinary & necessary for freelance/side-hustle business operations under IRS / standard freelance rules),
  "rationale": "Clear, concise 1-sentence explanation of classification and tax deductibility"
}

Strict Rules:
1. Return ONLY the JSON object. Do not include markdown commentary, conversational fillers, or preamble.
2. Standardize 'party' (e.g., 'Vercel Inc.', 'Stripe', 'Amazon', 'Client Acme').
3. For 'amount', extract pure floating-point number without currency symbols.
4. If income, 'party' is the client/platform paying. If expense, 'party' is the vendor.
5. All software hosting, developer tools, domains, contractor fees, and client equipment are tax-deductible business expenses. Personal items are not.
"""

def heuristic_fallback_parse(memo: str) -> Dict[str, Any]:
    """Graceful local fallback parser in case Ollama is not running."""
    text_lower = memo.lower()
    
    # Detect type
    is_income = any(w in text_lower for w in ["received", "invoice", "client paid", "got paid", "earned", "revenue", "deposit", "stripe payout"])
    tx_type = "income" if is_income else "expense"
    
    # Extract amount (prioritize currency prefix or keywords like 'for $99.99')
    amount = 0.0
    num_pattern = r"(\d+(?:,\d{3})*\.\d{1,2}|\d+(?:,\d{3})*)"
    currency_match = re.search(r"[\$€£]\s*" + num_pattern, memo)
    if not currency_match:
        currency_match = re.search(num_pattern + r"\s*(?:usd|eur|gbp|cad|dollars)", memo, re.IGNORECASE)
    if not currency_match:
        currency_match = re.search(r"(?:for|of|paid|received|invoiced|spent)\s*[\$€£]?\s*" + num_pattern, memo, re.IGNORECASE)
    if not currency_match:
        currency_match = re.search(r"\b(\d+\.\d{2})\b", memo)
    if not currency_match:
        currency_match = re.search(r"\b(\d+)\b", memo)

    if currency_match:
        try:
            amount = float(currency_match.group(1).replace(",", ""))
        except ValueError:
            amount = 0.0
            
    # Extract currency
    currency = "USD"
    if "€" in memo or "eur" in text_lower:
        currency = "EUR"
    elif "£" in memo or "gbp" in text_lower:
        currency = "GBP"
    elif "cad" in text_lower:
        currency = "CAD"

    # Detect party
    party = "Unknown Entity"
    vendors = ["vercel", "aws", "openai", "anthropic", "github", "stripe", "apple", "google", "figma", "notion", "digitalocean", "cloudflare", "amazon", "starbucks", "upwork"]
    for v in vendors:
        if v in text_lower:
            party = v.capitalize()
            break
            
    # Category and tax deductibility
    if is_income:
        category = "Freelance Income"
        tax_deductible = False
        rationale = "Recognized taxable business revenue from client or platform services."
    else:
        if any(w in text_lower for w in ["hosting", "cloud", "server", "aws", "vercel", "domain"]):
            category = "Cloud Hosting & Infrastructure"
            tax_deductible = True
            rationale = "Hosting and cloud services are ordinary and necessary business expenses."
        elif any(w in text_lower for w in ["software", "saas", "github", "copilot", "cursor", "notion", "figma"]):
            category = "Software & SaaS"
            tax_deductible = True
            rationale = "Software licenses used for side hustle product delivery are 100% tax deductible."
        elif any(w in text_lower for w in ["hardware", "macbook", "laptop", "mouse", "keyboard", "monitor"]):
            category = "Hardware & Equipment"
            tax_deductible = True
            rationale = "Work equipment and peripheral devices qualify as business deductions."
        else:
            category = "General Operating Expense"
            tax_deductible = True
            rationale = "Operating expense for business workflows."

    return {
        "type": tx_type,
        "party": party,
        "amount": amount,
        "currency": currency,
        "category": category,
        "tax_deductible": tax_deductible,
        "rationale": f"[Fallback Engine] {rationale}"
    }

# ==========================================
# REST API ENDPOINTS
# ==========================================

@app.get("/api/health")
async def check_health():
    """Verify local Ollama status and list available open-weight models."""
    ollama_online = False
    models = []
    active_model = OLLAMA_MODEL
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                ollama_online = True
                data = resp.json()
                models = [m.get("name") for m in data.get("models", [])]
    except Exception:
        ollama_online = False

    return {
        "status": "online",
        "ollama_online": ollama_online,
        "ollama_url": OLLAMA_BASE_URL,
        "configured_model": active_model,
        "available_models": models
    }

@app.post("/api/parse", response_model=ParsedTransaction)
async def parse_memo(req: ParseRequest):
    """
    Parses natural language transaction memos using local Ollama model (Qwen 2.5 / Llama 3.2).
    Enforces deterministic JSON output.
    """
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"Transaction memo to analyze: \"{req.memo}\"\nExtract structured JSON:",
        "system": EXTRACTION_SYSTEM_PROMPT,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,  # Low temperature for strict determinism
            "top_p": 0.9
        }
    }

    raw_response_text = ""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
            if resp.status_code == 200:
                resp_json = resp.json()
                raw_response_text = resp_json.get("response", "").strip()
            else:
                # Fallback to local heuristic if model returns non-200
                fallback_data = heuristic_fallback_parse(req.memo)
                return ParsedTransaction(**fallback_data)

    except (httpx.ConnectError, httpx.TimeoutException, Exception) as exc:
        # If Ollama is offline or timing out, switch to fallback seamlessly with notice
        fallback_data = heuristic_fallback_parse(req.memo)
        fallback_data["rationale"] += f" (Ollama offline: {str(exc)[:50]})"
        return ParsedTransaction(**fallback_data)

    # Clean markdown if model outputs ```json ... ```
    cleaned = raw_response_text
    if "```" in cleaned:
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
        # Normalize and validate fields
        return ParsedTransaction(
            type="income" if data.get("type", "").lower() == "income" else "expense",
            party=str(data.get("party", "Unknown")).strip(),
            amount=abs(float(data.get("amount", 0.0))),
            currency=str(data.get("currency", "USD")).upper(),
            category=str(data.get("category", "General")),
            tax_deductible=bool(data.get("tax_deductible", False)),
            rationale=str(data.get("rationale", "Extracted via Qwen 2.5 local model."))
        )
    except Exception:
        # Fallback if model returned invalid JSON structure
        fallback_data = heuristic_fallback_parse(req.memo)
        return ParsedTransaction(**fallback_data)

@app.get("/api/entries", response_model=List[TransactionOut])
def get_entries(
    type: Optional[str] = Query(None, pattern="^(income|expense)$"),
    tax_deductible: Optional[bool] = None,
    search: Optional[str] = None
):
    """Retrieve filtered ledger transactions."""
    query = "SELECT * FROM transactions WHERE 1=1"
    params = []

    if type:
        query += " AND type = ?"
        params.append(type)
    if tax_deductible is not None:
        query += " AND tax_deductible = ?"
        params.append(1 if tax_deductible else 0)
    if search:
        query += " AND (party LIKE ? OR category LIKE ? OR rationale LIKE ? OR raw_memo LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term, term, term])

    query += " ORDER BY date DESC, id DESC"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
        return [
            TransactionOut(
                id=r["id"],
                date=r["date"],
                type=r["type"],
                party=r["party"],
                amount=r["amount"],
                currency=r["currency"],
                category=r["category"],
                tax_deductible=bool(r["tax_deductible"]),
                rationale=r["rationale"],
                raw_memo=r["raw_memo"],
                created_at=r["created_at"]
            )
            for r in rows
        ]

@app.post("/api/entries", response_model=TransactionOut)
def create_entry(entry: TransactionCreate):
    """Commit a validated transaction into the local SQLite ledger."""
    entry_date = entry.date or datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO transactions (date, type, party, amount, currency, category, tax_deductible, rationale, raw_memo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry_date,
            entry.type.lower(),
            entry.party.strip(),
            abs(float(entry.amount)),
            entry.currency.upper(),
            entry.category.strip(),
            1 if entry.tax_deductible else 0,
            entry.rationale,
            entry.raw_memo
        ))
        conn.commit()
        new_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM transactions WHERE id = ?", (new_id,)).fetchone()
        return TransactionOut(
            id=row["id"],
            date=row["date"],
            type=row["type"],
            party=row["party"],
            amount=row["amount"],
            currency=row["currency"],
            category=row["category"],
            tax_deductible=bool(row["tax_deductible"]),
            rationale=row["rationale"],
            raw_memo=row["raw_memo"],
            created_at=row["created_at"]
        )

@app.delete("/api/entries/{entry_id}")
def delete_entry(entry_id: int):
    """Delete a single transaction by ID."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM transactions WHERE id = ?", (entry_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Transaction not found")
    return {"status": "success", "deleted_id": entry_id}

@app.delete("/api/entries")
def clear_all_entries():
    """Clear all records from the ledger."""
    with get_db() as conn:
        conn.execute("DELETE FROM transactions")
        conn.commit()
    return {"status": "cleared", "message": "All transactions purged"}

@app.get("/api/dashboard")
def get_dashboard_metrics():
    """Calculate real-time financial totals, profit, write-offs, and category breakdown."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM transactions").fetchall()

    total_income = sum(r["amount"] for r in rows if r["type"] == "income")
    total_expenses = sum(r["amount"] for r in rows if r["type"] == "expense")
    net_profit = total_income - total_expenses
    tax_deductible_total = sum(r["amount"] for r in rows if r["type"] == "expense" and r["tax_deductible"] == 1)
    estimated_tax_savings = tax_deductible_total * ESTIMATED_TAX_RATE

    # Category breakdown
    categories: Dict[str, float] = {}
    for r in rows:
        cat = r["category"]
        amt = r["amount"] if r["type"] == "expense" else -r["amount"]
        categories[cat] = categories.get(cat, 0.0) + amt

    return {
        "total_income": round(total_income, 2),
        "total_expenses": round(total_expenses, 2),
        "net_profit": round(net_profit, 2),
        "tax_deductible_total": round(tax_deductible_total, 2),
        "estimated_tax_savings": round(estimated_tax_savings, 2),
        "transaction_count": len(rows),
        "category_breakdown": {k: round(v, 2) for k, v in sorted(categories.items(), key=lambda x: abs(x[1]), reverse=True)}
    }

@app.get("/api/export")
def export_csv():
    """Exports the entire local financial ledger as a CSV file for tax reporting."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM transactions ORDER BY date ASC, id ASC").fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Transaction ID",
        "Date",
        "Type",
        "Vendor / Client",
        "Amount",
        "Currency",
        "Category",
        "Tax Deductible",
        "CPA Rationale",
        "Original Memo",
        "Recorded At"
    ])

    for r in rows:
        writer.writerow([
            r["id"],
            r["date"],
            r["type"].upper(),
            r["party"],
            f"{r['amount']:.2f}",
            r["currency"],
            r["category"],
            "YES" if r["tax_deductible"] else "NO",
            r["rationale"] or "",
            r["raw_memo"] or "",
            r["created_at"]
        ])

    csv_data = output.getvalue()
    filename = f"hustlevault_ledger_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

# ==========================================
# STATIC FRONTEND MOUNTING
# ==========================================
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
def read_root():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h2>HustleVault AI: static/index.html not found.</h2>", status_code=404)

if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*60)
    print("  🚀 HustleVault AI - Local Financial Copilot")
    print(f"  • Local Dashboard:  http://localhost:8000")
    print(f"  • Ollama Endpoint:  {OLLAMA_BASE_URL} (Model: {OLLAMA_MODEL})")
    print(f"  • Local Database:   {DB_FILE}")
    print("="*60 + "\n")
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
