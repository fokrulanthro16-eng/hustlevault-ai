from fastapi.testclient import TestClient
from app import app, init_db, clear_all_entries

def run_smoke_test():
    print("--- STARTING SMOKE TEST ---")
    init_db()
    clear_all_entries()
    client = TestClient(app)

    # 1. Health
    h = client.get("/api/health")
    assert h.status_code == 200, f"Health check failed: {h.status_code}"
    print("[OK] /api/health:", h.json().get("status"))

    # 2. Add sample income
    r_inc = client.post("/api/entries", json={
        "date": "2026-10-15",
        "type": "income",
        "party": "Acme Corp",
        "amount": 4500.0,
        "currency": "USD",
        "category": "Freelance Income",
        "tax_deductible": False,
        "rationale": "Frontend React & AI Integration Milestone",
        "raw_memo": "Received $4,500 from Acme Corp"
    })
    assert r_inc.status_code == 200
    print("[OK] POST /api/entries (income): ID", r_inc.json()["id"])

    # 3. Add sample expense
    r_exp = client.post("/api/entries", json={
        "date": "2026-10-15",
        "type": "expense",
        "party": "Vercel Inc.",
        "amount": 45.0,
        "currency": "USD",
        "category": "Cloud Hosting",
        "tax_deductible": True,
        "rationale": "Vercel Pro plan for production client deployments",
        "raw_memo": "Paid $45 to Vercel for Pro hosting"
    })
    assert r_exp.status_code == 200
    print("[OK] POST /api/entries (expense): ID", r_exp.json()["id"])

    # 4. Verify dashboard calculations
    dash = client.get("/api/dashboard").json()
    assert dash["total_income"] == 4500.0
    assert dash["total_expenses"] == 45.0
    assert dash["net_profit"] == 4455.0
    assert dash["tax_deductible_total"] == 45.0
    assert dash["estimated_tax_savings"] == 11.25
    print("[OK] GET /api/dashboard: Net Profit =", dash["net_profit"])

    # 5. Verify CSV export
    exp = client.get("/api/export")
    assert exp.status_code == 200
    lines = exp.text.strip().splitlines()
    assert len(lines) == 3  # header + 2 rows
    print("[OK] GET /api/export: Exported lines =", len(lines))

    # 6. Parse endpoint
    memo = "Bought ergonomic chair on Amazon for $250.00 for home workstation"
    parsed = client.post("/api/parse", json={"memo": memo}).json()
    assert parsed["amount"] == 250.0, f"Expected 250.0, got {parsed}"
    assert parsed["party"] == "Amazon"
    print("[OK] POST /api/parse: Party =", parsed["party"], "Amount =", parsed["amount"])

    # 7. Root UI
    root = client.get("/")
    assert root.status_code == 200
    assert "HustleVault" in root.text
    print("[OK] GET / (Static Index): Dashboard Loaded")

    # Reset ledger to clean state
    clear_all_entries()
    print("--- ALL SMOKE TESTS PASSED PERFECTLY ---")

if __name__ == "__main__":
    run_smoke_test()
