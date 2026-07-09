# Mapping UPI
### A Data Study of Scale, Geography, and Competition in India's Payment Ecosystem


**Period:** 2016 – 2026
**Data Sources:** NPCI Product Statistics · RBI Payment System Indicators
**Tools:** Python · MySQL 8.0 · Jupyter · matplotlib · seaborn

---

## Overview

India's Unified Payments Interface processed
23 billion transactions in a single month by 2026 —
more than the entire global SWIFT network handles
in two days. This project analyzes a decade of UPI
data across three dimensions: ecosystem dominance,
geographic inclusion, and market concentration.

---

## Findings

### Finding 1 — From Experiment to Essential Infrastructure
UPI grew at a 53.1% annual CAGR in transaction value —
four times faster than NEFT (15.7%) and RTGS (13.2%).
Its share of India's total digital payment value rose
from 1.8% in 2019 to 10.9% in 2026, crossing the 10%
threshold in April 2024. Average transaction value
declined from ₹4,857 (demonetisation peak, 2016) to
₹1,299 (2026) — a signature of mass adoption rather
than platform degradation.

### Finding 2 — The Northeast Leapfrog
Contrary to the standard digital divide narrative,
India's most historically underserved states are
its fastest-growing UPI markets. Northeast states
averaged 48.5% CAGR vs a national average of 32.2%
and a non-Northeast average of 25.6%. Pearson
correlation between starting volume and CAGR:
r = −0.774 — statistically robust confirmation
that low-base states are converging fastest.

### Finding 3 — The Democratization Paradox
UPI's open infrastructure produced a closed market.
PhonePe (46.5%) and Google Pay (32.9%) collectively
control 79.5% of all UPI transactions. The
Herfindahl-Hirschman Index stands at 3,335 —
above the US DOJ's 2,500 threshold for highly
concentrated markets — and has never dipped below
2,500 since measurement began in June 2020.

**Sub-finding — B2B UPI:** B2B UPI represents 0.01%
of the value processed by NEFT and RTGS combined.
The UPI revolution was retail-only.

---

## Repository Structure

```
UPI_Mapping/
├── analysis/
│   ├── finding1_upi_dominance.ipynb   # Finding 1 — Scale & Dominance
│   ├── finding2_statewise.ipynb       # Finding 2 — Northeast Leapfrog
│   ├── finding2_b2b.ipynb             # Sub-finding — B2B UPI
│   └── finding3_hhi.ipynb             # Finding 3 — Democratization Paradox
├── scraper/
│   ├── main.py                        # NPCI data scraper
│   └── load_to_mysql.py               # CSV to MySQL loader
├── report/
│   └── upi_report                    
├── requirements.txt
├── .gitignore
└── README.md
```

---

## Data Sources

| Source | Data | Period |
|--------|------|--------|
| NPCI Product Statistics | UPI aggregate monthly volume, value, bank count | 2016–2026 |
| NPCI Ecosystem Statistics | App-wise and state-wise monthly breakdowns | 2020–2026 |
| RBI Payment System Indicators | NEFT and RTGS monthly volume and value | 2019–2026 |

Data is not included in this repository.
NPCI data is collected via scraper/main.py.
RBI data has no equivalent public API and must
be collected manually — see step 3 below.

---

## Setup and Usage

**1. Install dependencies**
```bash
pip install -r requirements.txt
```

**2. Collect NPCI data**
```bash
cd scraper
python main.py
```
Downloads and parses NPCI data into scraper/output/
as structured CSVs. Raw files cached in scraper/raw/.

**3. Collect RBI data (manual)**

RBI has no scrape-friendly public API like NPCI's, so NEFT and RTGS
figures must be collected by hand from RBI's Payment System Indicators
(rbi.org.in → Statistics → Data Releases). This project used monthly
data from November 2019 onward, matching the earliest month NEFT/RTGS
figures were available alongside UPI's.

Reshape whatever you download into this format and save it as
`scraper/output/rbi_instruments.csv`:

```
month_year,instrument,volume_mn,value_cr
2019-11,NEFT,219.4594,1734651.2269
2019-11,RTGS,31.5561,987654.3210
```

- `month_year` — YYYY-MM
- `instrument` — NEFT or RTGS
- `volume_mn` — monthly transaction volume, millions
- `value_cr` — monthly transaction value, ₹ crore

This repo does not ship an automated cleaner for RBI's raw export —
unlike NPCI's, its format isn't stable enough to script reliably
against, so this step is manual by design.

**4. Set up MySQL**

Create the database:
```sql
CREATE DATABASE upi_data;
```

Load all CSVs into MySQL:
```bash
python scraper/load_to_mysql.py \
  --host localhost \
  --user your_username \
  --password your_password \
  --database upi_data
```

**5. Run analysis**

Open notebooks in Jupyter in this order:
```
analysis/finding1_upi_dominance.ipynb
analysis/finding2_statewise.ipynb
analysis/finding2_b2b.ipynb
analysis/finding3_hhi.ipynb
```
Each notebook connects to MySQL, runs the
analysis, and saves charts to analysis/output/.

---

## Methodology

**CAGR** — Compound Annual Growth Rate:
(V_last / V_first)^(1/n) − 1
where n = years elapsed between observations.

**HHI** — Herfindahl-Hirschman Index:
Sum of squared percentage market shares (scale 0–10,000).
Thresholds per US DOJ guidelines:
below 1,500 competitive · 1,500–2,500 moderate ·
above 2,500 highly concentrated.

**State CAGR** — calculated over April 2023 to
March 2026 using first and last available monthly
volume per state. States with starting volume
below 1 Mn transactions annotated with asterisk
due to small base effect.

---

## Report

The full analytical report —
"Mapping UPI: A Data Study of Scale, Geography,
and Competition in India's Payment Ecosystem" —
is available in the report/ folder.

---

## License

Data sourced from NPCI and RBI public statistics
portals. Analysis and visualizations by Ayush Chaugule.
