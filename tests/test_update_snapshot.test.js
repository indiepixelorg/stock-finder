import assert from "node:assert/strict";
import test from "node:test";
import * as snapshot from "../scripts/update_snapshot.js";

function fact(val, { start, end, filed, form, fp, accn, qtrs } = {}) {
  return { val, start, end, filed, form, fp, accn, qtrs };
}

test("reconstructs TTM flow from annual plus current and prior YTD", () => {
  const entries = [
    fact(100, { start: "2024-01-01", end: "2024-12-31", filed: "2025-02-15", form: "10-K", fp: "FY", accn: "annual" }),
    fact(40, { start: "2024-01-01", end: "2024-06-30", filed: "2024-08-01", form: "10-Q", fp: "Q2", accn: "prior" }),
    fact(55, { start: "2025-01-01", end: "2025-06-30", filed: "2025-08-01", form: "10-Q", fp: "Q2", accn: "current" }),
  ];
  const [value, evidence] = snapshot.ttmFlow(entries);
  assert.equal(value, 115);
  assert.equal(evidence.accn, "current");
});

test("annual history keeps the newest filing for each period", () => {
  const entries = [
    fact(90, { start: "2024-01-01", end: "2024-12-31", filed: "2025-02-01", form: "10-K", accn: "old" }),
    fact(100, { start: "2024-01-01", end: "2024-12-31", filed: "2025-03-01", form: "10-K", accn: "new" }),
    fact(80, { start: "2023-01-01", end: "2023-12-31", filed: "2024-02-01", form: "10-K", accn: "prior" }),
  ];
  const history = snapshot.annualHistory(entries);
  assert.deepEqual(history.map((entry) => entry.val), [100, 80]);
});

test("derives free cash flow and total debt from selected SEC facts", () => {
  const instant = (val, filed = "2026-02-01") => [fact(val, { end: "2025-12-31", filed, form: "10-K", accn: filed })];
  const annual = (val) => [fact(val, {
    start: "2025-01-01", end: "2025-12-31", filed: "2026-02-01", form: "10-K", fp: "FY", accn: "annual",
  })];
  const companyFacts = { facts: {
    "us-gaap": {
      RevenueFromContractWithCustomerExcludingAssessedTax: { units: { USD: annual(500) } },
      OperatingIncomeLoss: { units: { USD: annual(100) } },
      NetIncomeLoss: { units: { USD: annual(80) } },
      NetCashProvidedByUsedInOperatingActivities: { units: { USD: annual(70) } },
      PaymentsToAcquirePropertyPlantAndEquipment: { units: { USD: annual(20) } },
      EarningsPerShareDiluted: { units: { "USD/shares": annual(4) } },
      CashAndCashEquivalentsAtCarryingValue: { units: { USD: instant(50) } },
      Assets: { units: { USD: instant(1000) } },
      Liabilities: { units: { USD: instant(600) } },
      StockholdersEquity: { units: { USD: instant(400) } },
      DebtCurrent: { units: { USD: instant(30) } },
      LongTermDebtNoncurrent: { units: { USD: instant(170) } },
    },
    dei: { EntityCommonStockSharesOutstanding: { units: { shares: instant(10) } } },
  } };
  const values = snapshot.buildFinancials(companyFacts);
  assert.equal(values.free_cash_flow_ttm, 50);
  assert.equal(values.free_cash_flow_fy0, 50);
  assert.equal(values.total_debt, 200);
  assert.equal(values.shares_outstanding, 10);
});
