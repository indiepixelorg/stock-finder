# Value Stock Weekly

Value Stock Weekly is a weekly stock-research product for long-term investors. It presents 10 S&P 500 companies whose business quality and current valuation make them worth researching further.

The product is designed to answer three questions quickly:

1. Which companies are worth researching this week?
2. Why does each company appear attractively priced?
3. What changed since the previous weekly edition, and what could invalidate the thesis?

## Product concept

Rather than encouraging daily trading, Value Stock Weekly publishes a stable weekly shortlist based on the latest available market and fundamental data. A stock can remain on the list when its underlying thesis has not materially changed.

The product is a research and education tool. It does not manage portfolios, calculate investment budgets, execute trades, or provide personalized investment advice.

## Current page

The current page is a single weekly dashboard containing:

- A weekly edition heading and update date
- A ranked table of 10 S&P 500 companies
- Current share price
- Attractiveness assessment from `Fair` to `Excellent`
- Business-quality assessment
- Expandable company rows for deeper analysis
- A methodology note explaining the limits of the quantitative screen
- A free-beta email signup for the weekly shortlist
- Links to methodology, archive, contact, terms, and privacy pages

The visual direction is calm and editorial: dark navy typography, a light background, restrained colors, generous spacing, and a research-oriented layout rather than a brokerage terminal.

## Weekly research workflow

Each edition should make the selection process visible and explainable:

The `universe.csv` input is sourced from the [S&P 500 Companies dataset](https://github.com/datasets/s-and-p-500-companies/tree/main).

```text
S&P 500 universe
        ↓
Business-quality review
        ↓
Valuation review
        ↓
Risk review
        ↓
10-stock weekly shortlist
```

The page should show relevant weekly context, such as:

- Number of companies screened
- Number selected
- New additions
- Unchanged companies
- Removals from the previous edition
- Data as-of date and source coverage

These figures should only be displayed once they are calculated from real data. Placeholder values should be clearly marked as illustrative.

## Price snapshot

Approximate weekly valuation prices come from clean daily bars published by
[HF Data Library](https://hfdatalibrary.com/pages/data). Recent bars are based
on trades executed on IEX and can differ from the official consolidated close.

Register for a free HF Data Library API key and keep it outside the repository:

```sh
python3 -m pip install -r requirements.txt
export HF_DATA_API_KEY="your-key"
python3 scripts/update_prices.py
```

For a small smoke test, process only the first five universe rows:

```sh
python3 scripts/update_prices.py --limit 5 --output /tmp/latest_prices.csv
```

The script atomically overwrites `data/latest_prices.csv`. It retains one row
per universe security, calculates `valuation_price` as the median of up to five
recent daily closes, and marks unusable rows as `excluded` with a reason. A
price requires at least three observations in the trailing 10 calendar days,
and its newest observation must be no more than seven calendar days old.

Publications and derived work using this snapshot must credit HF Data Library
under CC BY 4.0 and include the required IEX attribution:

> Data provided for free by IEX. By accessing or using IEX Historical Data,
> you agree to the IEX Historical Data Terms of Use.

## Valuation screen

After refreshing the SEC and price snapshots, build the joined valuation table:

```sh
python3 scripts/build_screen.py
```

The script atomically overwrites `data/latest_screen.csv` and retains one row
per universe security. It calculates market capitalization, enterprise value,
earnings and FCF yields, valuation multiples, operating margin, annualized
revenue growth across the five fiscal-year slots, positive FCF years, and
five-year-window median FCF and net income.

Missing inputs are never treated as zero. Negative earnings and FCF remain
visible through negative yields, while P/E, price-to-FCF, and other multiples
with non-positive denominators are left blank. `calculation_status` is `ok`
only when all requested metrics are available; `partial` rows retain warnings
for review, and companies without usable prices are `excluded`.

Market capitalization is approximate because SEC shares outstanding can lag
the price date or represent complex share classes imperfectly. The source
values, dates, URLs, status, and warnings remain in each row for auditing.

## Weekly research shortlist

Build the ranked weekly shortlist after refreshing `data/latest_screen.csv`:

```sh
python3 scripts/rank_companies.py
```

The script atomically overwrites `data/latest_top10.csv`. Eligible companies
must have a complete valuation screen, positive five-year-window median FCF
and net income, and positive FCF in at least four reported fiscal years.
Financials and real estate are excluded because this general-purpose model
does not yet include their sector-specific valuation measures. Duplicate CIKs
are removed and no more than two selected companies may come from one sector.

The 0-100 attractiveness score uses these disclosed weights:

- Free-cash-flow yield: 25%
- Earnings yield: 20%
- EV/operating income: 15%
- Operating margin: 10%
- Five-year annualized revenue growth: 10%
- Positive-FCF consistency: 10%
- Net debt/FCF: 10%

The output also includes a separate quantitative quality score for display. It
does not affect shortlist order and is calculated as 40% operating-margin
percentile, 35% positive-FCF consistency, and 25% revenue-growth percentile.
The stored score is 0-100 and the displayed score is 0-10. Display labels are
`Strong` at 8.5 or higher, `Good` from 7.0, `Fair` from 5.5, and `Weak` below
5.5. Leverage is deliberately excluded because SEC debt-tag coverage can be
incomplete for some companies.

Valuation and operating-margin percentiles are calculated within sectors when
at least five eligible companies are available; smaller sectors use the global
eligible-company distribution. The CSV includes every component score, the
three largest positive score contributors, review flags for extreme yields,
and source URLs for auditability. Review flags do not automatically exclude a
company; they identify inputs or unusual events that should be checked before
publication.

This is a mechanical research screen, not a fair-value estimate or investment
recommendation. It can surface cyclical peaks, one-off cash flows, stale SEC
share counts, or companies whose risks are not represented by these metrics.
Each selected company still requires qualitative research and a review of its
latest filings before publication.

## Research notes

Translate the ranked metrics into deterministic, human-readable notes:

```sh
python3 scripts/build_research_notes.py
```

The script atomically overwrites `data/latest_research.csv`. Each row contains
separate explanations for selection, valuation, business quality, historical
growth, balance-sheet leverage, numerical warnings, and items to verify in the
latest 10-K or 10-Q. The prose is generated entirely from disclosed rules and
the values in `data/latest_top10.csv` and `data/latest_screen.csv`; it does not
invent news, management assessments, or company-specific qualitative risks.

`priority_review` identifies companies with numerical anomalies such as
extreme yields, TTM results far above five-year medians, elevated leverage, or
negative historical revenue growth. It also flags unusually low reported debt
relative to equity because SEC XBRL tag coverage can omit material borrowings
or leases. `standard_review` means no threshold was crossed, not that manual
research can be skipped. Every company must still receive a filing and
qualitative-risk review before public presentation.

## Static site

Generate the responsive GitHub Pages site after building the research notes:

```sh
python3 scripts/build_site.py
```

The script reads `data/latest_research.csv` and atomically generates
`docs/index.html`, `docs/assets/styles.css`, `docs/assets/site.js`, and
`docs/.nojekyll`. It requires exactly 10 uniquely ranked research rows and
valid source links, so an incomplete shortlist cannot silently replace the
existing page. The publication timestamp uses the current Europe/Ljubljana
time truncated to the hour.

The editable page structure lives in `templates/`, and the editable CSS and
JavaScript live in `assets/`. The generated `docs/` directory can be published
directly with GitHub Pages. The email form is intentionally a non-submitting
preview, and unfinished navigation links display a `Coming soon` message.

For a reproducible local build or automated test, provide an ISO-8601 time:

```sh
python3 scripts/build_site.py --published-at 2026-08-22T17:00:00+02:00
```

## Company row expansion

Selecting a company row opens a full-width research panel. The expanded view should prioritize explanation over raw data.

Recommended sections:

1. **What changed since last week?**
   - Ranking movement
   - Price change
   - Valuation change
   - Fundamental or earnings updates

2. **Valuation snapshot**
   - Current price
   - Estimated fair-value range
   - Discount or premium to fair value
   - Relevant valuation ratios

3. **Financial trend**
   - Five-year revenue trend
   - Earnings or operating-income trend
   - Five-year free-cash-flow trend

4. **Why this is a quality business**
   - Profitability
   - Cash-flow consistency
   - Growth
   - Balance-sheet strength

5. **Why the current price appears attractive**
   - Historical valuation comparison
   - Peer comparison where appropriate
   - Fair-value assumptions

6. **Main risk to the investment thesis**
   - What could invalidate the thesis
   - Which metric should be monitored
   - What future event could change the analysis

Fair value should be shown as a range rather than a falsely precise point estimate. Explanations should be grounded in structured financial data and public sources.

## Ratings language

### Valuation

The valuation column uses a positive-only scale for companies that pass the shortlist criteria:

1. Fair
2. Good
3. Attractive
4. Strong
5. Excellent

Higher ratings indicate a more attractive current price relative to estimated fair value.

### Business quality

Business quality should not be presented as an unexplained number alone. It should be supported by a profile covering profitability, cash flow, growth, balance-sheet strength, and earnings stability.

### Risk

Risk should remain separate from both quality and valuation. A high-quality company can still carry significant investment risk, and a cheap stock can still be a weak business.

## Beta email signup

The beta is free. Visitors can sign up to receive the weekly shortlist by email:

> Receive 10 companies worth researching each Monday, with valuation explanations, weekly changes, and key risks.

The MVP does not include payments, subscriptions, brokerage links, portfolio management, or budget tools.

## MVP scope

### Included

- S&P 500 universe
- Weekly edition
- 10-stock shortlist
- Transparent valuation and quality labels
- Risk explanation
- Expandable research rows
- Methodology page
- Weekly archive
- Free email signup

### Not included yet

- Brokerage integration
- Trading or execution
- Personalized recommendations
- Portfolio tracking
- Budget allocation
- Daily signals
- Advanced filters and sorting
- Paid subscriptions
- Full company research pages beyond the expanded row

## Trust principles

Value Stock Weekly should:

- Distinguish facts from interpretations
- Show data as-of dates
- Explain how rankings are formed
- Use fair-value ranges and disclose assumptions
- Show risks with equal prominence to potential upside
- Preserve previous weekly editions
- Avoid unsupported claims such as “guaranteed,” “sure thing,” or “best stock to buy”
- Clearly state that the service is for general research and educational purposes

## Future direction

Once the free beta demonstrates recurring interest, possible extensions include:

- A premium weekly research edition
- Watchlists and thesis-change alerts
- Deeper company research pages
- Expanded stock universes beyond the S&P 500
- Historical thesis and ranking performance
- Reports for advisors, newsletters, or investment communities

The long-term product goal is to help investors understand when a stock’s market price may be disconnected from the quality and earning power of the underlying business.
