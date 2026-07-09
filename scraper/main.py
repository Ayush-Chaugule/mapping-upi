#!/usr/bin/env python3
"""
NPCI UPI Data Scraper
=====================
Downloads and parses UPI statistics from
npci.org.in into structured CSVs for analysis.

Sources:
  - npci.org.in/product/upi/product-statistics
  - npci.org.in/product/ecosystem-statistics/upi

Tables collected:
  - upi_aggregate   : Monthly volume + value (2016-2026)
  - upi_apps        : App-wise breakdown (2020-2026)
  - upi_mandate     : IPO mandate execution (2022-2026)
  - upi_statewise   : State-wise volume (2023-2026)

Output: CSV files in scraper/output/
Cache:  Raw files in scraper/raw/ (skip re-download)

Usage:
  pip install -r requirements.txt
  python main.py
"""

# Auto-install
import subprocess, sys
for _pkg, _imp in [("requests","requests"),("pandas","pandas"),("openpyxl","openpyxl")]:
    try: __import__(_imp)
    except ImportError:
        print(f"[SETUP] pip install {_pkg}")
        subprocess.check_call([sys.executable,"-m","pip","install",_pkg,"-q"])

import re, time, json, warnings, datetime as dt
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

import requests
import pandas as pd

warnings.filterwarnings("ignore")

# Paths
BASE_DIR   = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
RAW_DIR    = BASE_DIR / "raw"
OUTPUT_DIR.mkdir(exist_ok=True)
RAW_DIR.mkdir(exist_ok=True)

FAILED_LOG = BASE_DIR / "failed_downloads.txt"
ERRORS_LOG = BASE_DIR / "parse_errors.txt"
for _f in (FAILED_LOG, ERRORS_LOG):
    _f.write_text("", encoding="utf-8")

# Config
NPCI_BASE        = "https://www.npci.org.in"
# Ecosystem API  — month-by-month, product_name=UPI (uppercase)
ECOSYSTEM_API    = "https://www.npci.org.in/api/ecosystem-statistics/get-statistics"
# Product-stat API — financial-year-based, product_name=upi (lowercase)
PRODUCT_STAT_API = "https://www.npci.org.in/api/product-statistic/tab/detail"
# Products-statistics-files API — returns file URL, month-by-month, product_name=upi
PRODUCTS_FILES_API = "https://www.npci.org.in/api/products-statistics-files"

HEADERS = {
    "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept":       "application/json, text/plain, */*",
    "Referer":      "https://www.npci.org.in/what-we-do/upi/upi-ecosystem-statistics",
    "Origin":       "https://www.npci.org.in",
    "Accept-Language": "en-US,en;q=0.9",
}

DELAY     = 1.0   # seconds between API calls
PAGE_SIZE = 500   # fits all records in one shot for most tabs

_today        = dt.date.today()
CURRENT_YEAR  = _today.year
CURRENT_MONTH = _today.month

# Month abbreviations as ecosystem API expects (3-letter, capitalised)
MONTHS: List[Tuple[str, int]] = [
    ("Jan",1),("Feb",2),("Mar",3),("Apr",4),("May",5),("Jun",6),
    ("Jul",7),("Aug",8),("Sep",9),("Oct",10),("Nov",11),("Dec",12),
]

START_YEAR = 2016  # UPI launched August 2016

# Financial years for product-stat API (format: "2016-17", "2017-18", …)
# FY runs April→March; current FY start year depends on whether we're past April
_fy_start = CURRENT_YEAR if CURRENT_MONTH >= 4 else CURRENT_YEAR - 1
FY_RANGES: List[str] = [
    f"{y}-{str(y + 1)[-2:]}" for y in range(START_YEAR, _fy_start + 1)
]  # e.g. ["2016-17", "2017-18", …, "2026-27"]

# Tab configurations
# The 4 tabs kept from NPCI's ecosystem/product-statistics APIs:
#   ecosystem-statistics: UPI APPS, Upi Statewise Statistics
#   product-statistics:   Monthly Statistics, IPO Mandate Execution
TAB_CONFIGS: List[Dict] = [
    # UPI APPS — ecosystem tab
    {
        "category":  "app_wise",
        "csv":       "upi_apps.csv",
        "api":       ECOSYSTEM_API,
        "mode":      "ecosystem",
        "try_names": ["upi-apps"],
    },
    # UPI Statewise Statistics — ecosystem tab
    {
        "category":  "statewise",
        "csv":       "upi_statewise.csv",
        "api":       ECOSYSTEM_API,
        "mode":      "ecosystem",
        "try_names": ["statewise-statistic"],   # verified against NPCI ecosystem-statistics UI
    },
    # UPI tab (first tab on product-statistics) — data from FY 2016-17
    # Confirmed URL: /api/product-statistic/tab/detail?product_name=upi
    #   &tab_name=product-statistics-upi&year_range=2026-27&excel_type=monthly
    # Columns: month, no_of_banks_live_on_upi, volume_in_mn, value_in_cr
    {
        "category":   "aggregate",
        "csv":        "upi_aggregate.csv",
        "api":        PRODUCT_STAT_API,
        "mode":       "product_stat",
        "excel_type": "monthly",
        "try_names":  ["product-statistics-upi"],
    },
    # IPO Mandate Execution — product-statistic API, month/year params
    # Confirmed URL: /api/product-statistic/tab/detail?product_name=upi
    #                &tab_name=mandate-execution&month=May&year=2026
    # Uses calendar month+year (not year_range like monthly-statistics)
    {
        "category":  "mandate",
        "csv":       "upi_mandate.csv",
        "api":       PRODUCT_STAT_API,
        "mode":      "product_stat_monthly",
        "try_names": ["mandate-execution"],   # verified against NPCI product-statistics UI
    },
]

# Logging
def log_failed(key: str, err: str):
    with open(FAILED_LOG, "a", encoding="utf-8") as f:
        f.write(f"{key}\t{err}\n")

def log_error(key: str, err: str):
    with open(ERRORS_LOG, "a", encoding="utf-8") as f:
        f.write(f"{key}\t{err}\n")

# HTTP
_session = requests.Session()
_session.headers.update(HEADERS)


def fetch_api(api_url: str, tab_name: str, year: int, month_abbr: str,
              page: int = 1, category: str = "misc") -> Optional[Dict]:
    """
    Call the NPCI statistics API. Returns parsed JSON or None on error.
    Caches each response to RAW_DIR/<category>/ as a .json file.
    """
    cat_dir = RAW_DIR / category
    cat_dir.mkdir(exist_ok=True)
    cache_key = f"{tab_name}__{year}_{month_abbr}_p{page}"
    cache_path = cat_dir / f"{cache_key}.json"

    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass  # re-fetch if cache is corrupt

    params = {
        "product_name": "UPI",
        "tab_name":     tab_name,
        "year":         year,
        "month":        month_abbr,
        "page_no":      page,
        "sort_by":      "asc",
        "size":         PAGE_SIZE,
        "locale":       "en",
    }

    time.sleep(DELAY)
    try:
        r = _session.get(api_url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data
    except Exception:
        return None


def extract_records(response: Dict) -> List[Dict]:
    """
    Dig through various response shapes and return the list of data records.
    NPCI APIs typically wrap data in one of these shapes:
      { "data": [...] }
      { "data": { "content": [...] } }
      { "result": [...] }
      { "response": { "data": [...] } }
    """
    if not isinstance(response, dict):
        return []

    def _find_list(obj: Any) -> Optional[List]:
        if isinstance(obj, list) and len(obj) > 0 and isinstance(obj[0], dict):
            return obj
        if isinstance(obj, dict):
            # "results" is the actual NPCI data key; try it before brute-force
            # to avoid landing on table_headers.headers (the column definitions list)
            for k in ("results", "content", "data", "result", "records", "rows", "items", "list"):
                if k in obj:
                    found = _find_list(obj[k])
                    if found is not None:
                        return found
            # Brute-force: any key whose value is a non-empty list of dicts
            for v in obj.values():
                if not isinstance(v, dict):  # skip nested dicts (avoids table_headers)
                    continue
                found = _find_list(v)
                if found is not None:
                    return found
        return None

    return _find_list(response) or []


def get_total_pages(response: Dict) -> int:
    """Extract total page count from the API response."""
    for key in ("totalPages", "total_pages", "pageCount", "pages"):
        if key in response:
            try:
                return int(response[key])
            except Exception:
                pass
    # Try nested
    for v in response.values():
        if isinstance(v, dict):
            for key in ("totalPages", "total_pages", "pageCount"):
                if key in v:
                    try:
                        return int(v[key])
                    except Exception:
                        pass
    # Fall back: compute from totalCount (e.g. statewise API returns totalCount not totalPages)
    import math
    for v in (response, *[x for x in response.values() if isinstance(x, dict)]):
        for key in ("totalCount", "total_count", "totalRecords", "total_records"):
            if key in v:
                try:
                    total = int(v[key])
                    if total > PAGE_SIZE:
                        return math.ceil(total / PAGE_SIZE)
                except Exception:
                    pass
    return 1


def fetch_all_pages(api_url: str, tab_name: str, year: int, month_abbr: str,
                    category: str = "misc") -> List[Dict]:
    """Fetch all paginated records for a given tab/year/month."""
    first = fetch_api(api_url, tab_name, year, month_abbr, page=1, category=category)
    if first is None:
        return []
    records = extract_records(first)
    total_pages = get_total_pages(first)
    for page in range(2, total_pages + 1):
        resp = fetch_api(api_url, tab_name, year, month_abbr, page=page, category=category)
        if resp:
            records.extend(extract_records(resp))
    return records

# Product-statistic API (financial-year based)
def fetch_product_stat_pages(tab_name: str, fy_range: str,
                              excel_type: str = "monthly",
                              category: str = "misc") -> List[Dict]:
    """
    Fetch from the product-statistic API using financial-year params.
    GET /api/product-statistic/tab/detail
        ?product_name=upi&tab_name=...&year_range=2026-27&excel_type=monthly
        &page_no=1&page_size=500&locale=en
    """
    cat_dir = RAW_DIR / category
    cat_dir.mkdir(exist_ok=True)
    cache_key  = f"{tab_name}__{fy_range}"
    cache_path = cat_dir / f"{cache_key}.json"

    if cache_path.exists():
        try:
            resp = json.loads(cache_path.read_text(encoding="utf-8"))
            return extract_records(resp)
        except Exception:
            pass

    params = {
        "product_name": "upi",       # lowercase — different from ecosystem API
        "tab_name":     tab_name,
        "year_range":   fy_range,
        "excel_type":   excel_type,
        "page_no":      1,
        "page_size":    PAGE_SIZE,
        "locale":       "en",
    }

    time.sleep(DELAY)
    try:
        r = _session.get(PRODUCT_STAT_API, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return extract_records(data)
    except Exception:
        return []


# Product-statistic API — calendar month/year variant (mandate-execution)
def fetch_product_stat_monthly(tab_name: str, year: int, month_abbr: str,
                                category: str = "misc") -> List[Dict]:
    """
    product-statistic API but with month=May&year=2026 params (not year_range).
    Confirmed for mandate-execution tab.
    GET /api/product-statistic/tab/detail
        ?product_name=upi&tab_name=mandate-execution
        &month=May&year=2026&excel_type=&page_no=1&page_size=500&locale=en
    """
    cat_dir = RAW_DIR / category
    cat_dir.mkdir(exist_ok=True)
    cache_key  = f"{tab_name}__{year}_{month_abbr}"
    cache_path = cat_dir / f"{cache_key}.json"

    if cache_path.exists():
        try:
            return extract_records(json.loads(cache_path.read_text(encoding="utf-8")))
        except Exception:
            pass

    params = {
        "product_name": "upi",
        "tab_name":     tab_name,
        "month":        month_abbr,
        "year":         year,
        "excel_type":   "",
        "page_no":      1,
        "page_size":    PAGE_SIZE,
        "locale":       "en",
    }

    time.sleep(DELAY)
    try:
        r = _session.get(PRODUCT_STAT_API, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return extract_records(data)
    except Exception:
        return []


# Excel download helpers
def _extract_file_url(response: Dict) -> Optional[str]:
    """Recursively find a fileUrl / file_url in an API response and make it absolute."""
    if not isinstance(response, dict):
        return None
    for k, v in response.items():
        if "fileurl" in k.lower() or "file_url" in k.lower():
            if isinstance(v, str) and v.strip():
                url = v.strip()
                return (NPCI_BASE + url) if url.startswith("/") else url
        if isinstance(v, dict):
            found = _extract_file_url(v)
            if found:
                return found
    return None


def _download_excel(url: str, save_path: Path) -> bool:
    """Download an Excel file from url and save to save_path. Returns success."""
    if save_path.exists() and save_path.stat().st_size > 0:
        return True
    time.sleep(DELAY)
    try:
        r = _session.get(url, timeout=30)
        r.raise_for_status()
        save_path.write_bytes(r.content)
        return True
    except Exception:
        return False


def _parse_excel(path: Path) -> List[Dict]:
    """
    Flexibly parse an NPCI Excel file into a list of dicts.
    Finds the first row that looks like a header (multiple non-empty cells,
    at least one numeric column follows), then reads all data rows.
    """
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.active

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []

        # Find header row: first row with 3+ non-empty string cells
        header_idx = None
        for i, row in enumerate(rows):
            non_empty = [c for c in row if c is not None and str(c).strip()]
            if len(non_empty) >= 3:
                header_idx = i
                break
        if header_idx is None:
            return []

        headers = [str(c).strip() if c is not None else f"col_{i}"
                   for i, c in enumerate(rows[header_idx])]

        records = []
        for row in rows[header_idx + 1:]:
            if all(c is None or str(c).strip() == "" for c in row):
                continue
            rec = {headers[i]: row[i] for i in range(min(len(headers), len(row)))}
            records.append(rec)
        return records
    except Exception:
        return []


# products-statistics-files API (mandate-execution)
def fetch_files_api(tab_name: str, year: int, month_abbr: str,
                    category: str = "misc") -> List[Dict]:
    """
    Call /api/products-statistics-files, get the fileUrl, download and parse the Excel.
    Confirmed params: product_name=upi, tab_name=mandate-execution, month=May, year=2026
    """
    cat_dir = RAW_DIR / category
    cat_dir.mkdir(exist_ok=True)

    # Cache the JSON response
    json_cache = cat_dir / f"{tab_name}__{year}_{month_abbr}.json"
    if json_cache.exists():
        try:
            data = json.loads(json_cache.read_text(encoding="utf-8"))
        except Exception:
            data = None
    else:
        params = {
            "product_name": "upi",
            "tab_name":     tab_name,
            "month":        month_abbr,
            "year":         year,
            "excel_type":   "",
            "locale":       "en",
        }
        time.sleep(DELAY)
        try:
            r = _session.get(PRODUCTS_FILES_API, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
            json_cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return []

    # Try to get JSON records first
    records = extract_records(data)
    if records:
        return records

    # Fall back: extract fileUrl → download Excel → parse
    file_url = _extract_file_url(data)
    if not file_url:
        return []

    xlsx_path = cat_dir / f"{tab_name}__{year}_{month_abbr}.xlsx"
    if not _download_excel(file_url, xlsx_path):
        return []
    return _parse_excel(xlsx_path)


# Tab discovery
def discover_tab_name(cfg: Dict) -> Optional[str]:
    """
    Try each candidate tab_name and return the first one that returns real data.
    Handles both ecosystem (month-based) and product-stat (FY-based) modes.
    """
    mode      = cfg.get("mode", "ecosystem")
    api_url   = cfg["api"]
    try_names = cfg["try_names"]
    category  = cfg["category"]
    excel_type = cfg.get("excel_type", "monthly")

    probe_months = [("May", 2026), ("Dec", 2025), ("Dec", 2024)]

    if mode == "product_stat":
        probe_fys = ["2026-27", "2025-26", "2024-25"]
        for tab_name in try_names:
            for fy in probe_fys:
                records = fetch_product_stat_pages(tab_name, fy, excel_type, category)
                if records:
                    print(f"    Confirmed tab_name='{tab_name}'  ({len(records)} records for FY {fy})")
                    print(f"    Sample keys: {list(records[0].keys())}")
                    return tab_name

    elif mode == "product_stat_monthly":
        for tab_name in try_names:
            for month_abbr, year in probe_months:
                records = fetch_product_stat_monthly(tab_name, year, month_abbr, category)
                if records:
                    print(f"    Confirmed tab_name='{tab_name}'  ({len(records)} records for {month_abbr} {year})")
                    print(f"    Sample keys: {list(records[0].keys())}")
                    return tab_name

    elif mode == "files_api":
        for tab_name in try_names:
            for month_abbr, year in probe_months:
                records = fetch_files_api(tab_name, year, month_abbr, category)
                if records:
                    print(f"    Confirmed tab_name='{tab_name}'  ({len(records)} records for {month_abbr} {year})")
                    print(f"    Sample keys: {list(records[0].keys())}")
                    return tab_name

    else:  # ecosystem — also handles excel_fallback tabs
        excel_fallback = cfg.get("excel_fallback", False)
        for tab_name in try_names:
            for month_abbr, year in probe_months:
                records = fetch_all_pages(api_url, tab_name, year, month_abbr, category=category)
                if records:
                    print(f"    Confirmed tab_name='{tab_name}'  ({len(records)} records for {month_abbr} {year})")
                    print(f"    Sample keys: {list(records[0].keys())}")
                    return tab_name
                if excel_fallback:
                    # Even with empty results, check if the API returned a fileUrl
                    resp = fetch_api(api_url, tab_name, year, month_abbr, category=category)
                    if resp and _extract_file_url(resp):
                        print(f"    Confirmed tab_name='{tab_name}'  (via fileUrl for {month_abbr} {year})")
                        return tab_name
    return None

# Column extraction helpers
def _num(record: Dict, *patterns: str) -> Optional[float]:
    """Find the first key matching any pattern and return it as float."""
    for pat in patterns:
        for k, v in record.items():
            if pat.lower() in k.lower() and v is not None:
                s = re.sub(r"[,\s₹$%]", "", str(v))
                try:
                    return float(s)
                except ValueError:
                    pass
    return None


def _str(record: Dict, *patterns: str) -> Optional[str]:
    """Find the first key matching any pattern and return it as string."""
    for pat in patterns:
        for k, v in record.items():
            if pat.lower() in k.lower() and v:
                s = str(v).strip()
                if s and s.lower() not in ("null", "none", "na", "-"):
                    return s
    return None


def _cit_vol(r: Dict) -> Optional[float]:
    # NPCI key: customer_initiated_transactions_volume_mn
    return _num(r, "customer_initiated_transactions_volume", "customer_initiated_vol", "cit_vol")

def _cit_val(r: Dict) -> Optional[float]:
    # NPCI key: customer_initiated_transactions_value_cr
    return _num(r, "customer_initiated_transactions_value", "customer_initiated_val", "cit_val")

def _b2c_vol(r: Dict) -> Optional[float]:
    # NPCI key: b_2_c_transactions_volume_mn
    return _num(r, "b_2_c_transactions_vol", "b_2_c_vol", "b2c_vol", "b2cvol", "b2c_txn")

def _b2c_val(r: Dict) -> Optional[float]:
    return _num(r, "b_2_c_transactions_val", "b_2_c_val", "b2c_val", "b2cval", "b2c_amount")

def _b2b_vol(r: Dict) -> Optional[float]:
    # NPCI key: b_2_b_transactions_volume_mn
    return _num(r, "b_2_b_transactions_vol", "b_2_b_vol", "b2b_vol", "b2bvol", "b2b_txn")

def _b2b_val(r: Dict) -> Optional[float]:
    return _num(r, "b_2_b_transactions_val", "b_2_b_val", "b2b_val", "b2bval", "b2b_amount")

def _tot_vol(r: Dict) -> Optional[float]:
    # Try total first, then fall back to just vol/txn
    v = _num(r, "total_vol", "totalvol", "total_txn", "totaltxn", "total_count",
             "grand_vol", "grandvol")
    if v is not None:
        return v
    return _num(r, "volume", "vol_mn", "txn_vol", "no_of_txn", "count")

def _tot_val(r: Dict) -> Optional[float]:
    v = _num(r, "total_val", "totalval", "total_amount", "totalamount", "total_value",
             "grand_val", "grandval")
    if v is not None:
        return v
    return _num(r, "value", "val_cr", "amount", "txn_val")

# Row builders per category
def build_app_row(r: Dict, month_year: str) -> Optional[Dict]:
    # NPCI key is "application_name"; "application_name" contains "name" so _str finds it.
    # Explicit "application_name" first avoids accidentally matching "product_name"/"tab_name".
    app = _str(r, "application_name", "app_name", "bank_name", "member_name", "tpap_name", "psp_name")
    if not app:
        return None
    cit_v = _cit_vol(r); cit_a = _cit_val(r)
    b2c_v = _b2c_vol(r); b2c_a = _b2c_val(r)
    b2b_v = _b2b_vol(r); b2b_a = _b2b_val(r)
    tot_v = _tot_vol(r);  tot_a = _tot_val(r)
    if all(v is None for v in (cit_v, cit_a, b2c_v, b2c_a, b2b_v, b2b_a, tot_v, tot_a)):
        return None
    return {
        "month_year": month_year, "app_name": app,
        "cit_volume_mn": cit_v, "cit_value_cr": cit_a,
        "b2c_volume_mn": b2c_v, "b2c_value_cr": b2c_a,
        "b2b_volume_mn": b2b_v, "b2b_value_cr": b2b_a,
        "total_volume_mn": tot_v, "total_value_cr": tot_a,
    }


def build_statewise_row(r: Dict, month_year: str) -> Optional[Dict]:
    # Skip district-level rows — only keep state totals (district field is null/absent)
    if r.get("district"):
        return None
    # NPCI key: state_union_territory
    state = _str(r, "state_union_territory", "state_name", "state", "region", "circle", "location")
    if not state:
        return None
    # NPCI keys: volume_in_mn, value_in_cr
    vol = _num(r, "volume_in_mn", "volume_mn")
    val = _num(r, "value_in_cr", "value_cr")
    if vol is None and val is None:
        return None
    return {"month_year": month_year, "state": state, "volume_mn": vol, "value_cr": val}


_MONTH_NAME_MAP = {
    "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
    "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12,
    "january":1,"february":2,"march":3,"april":4,"june":6,
    "july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
}

def _parse_month_field(r: Dict) -> Optional[str]:
    """
    Parse a month field like "June-2026" or "Jun-2026" → "2026-06".
    Used for product-statistic API records which carry the month in the record.
    """
    raw = _str(r, "month", "period", "date")
    if not raw:
        return None
    # e.g. "June-2026" or "Jun 2026"
    m = re.match(r"([A-Za-z]+)[\s\-]+(\d{4})", raw.strip())
    if not m:
        return None
    mon = _MONTH_NAME_MAP.get(m.group(1).lower())
    if not mon:
        return None
    return f"{m.group(2)}-{mon:02d}"


def build_aggregate_row(r: Dict, month_year: str) -> Optional[Dict]:
    my = _parse_month_field(r) or month_year
    vol = _tot_vol(r); val = _tot_val(r)
    if vol is None and val is None:
        return None
    # NPCI key: no_of_banks_live_on_upi
    banks = _num(r, "no_of_banks_live_on_upi", "no_of_banks", "banks_on_upi", "banks")
    return {"month_year": my, "banks_on_upi": banks, "volume_mn": vol, "value_cr": val}


def build_mandate_row(r: Dict, month_year: str) -> Optional[Dict]:
    # Mandate execution is bank-wise IPO data (May only).
    # NPCI keys: remitter_bank, total_volume, approved, bd, td
    my = _parse_month_field(r) or month_year
    bank = _str(r, "remitter_bank", "bank_name", "bank")
    if not bank:
        return None
    vol      = _num(r, "total_volume")
    approved = _num(r, "approved")
    bd       = _num(r, "bd")
    td       = _num(r, "td")
    if vol is None and approved is None:
        return None
    return {
        "month_year":    my,
        "remitter_bank": bank,
        "total_volume":  vol,
        "approved_pct":  approved,
        "bd_pct":        bd,
        "td_pct":        td,
    }


ROW_BUILDER = {
    "app_wise":  build_app_row,
    "statewise": build_statewise_row,
    "aggregate": build_aggregate_row,
    "mandate":   build_mandate_row,
}

# Finalize DataFrame
def finalize(frames: List[pd.DataFrame], drop_na: List[str],
             dedup: List[str], sort_by: List[str]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=drop_na)
    df = df.drop_duplicates(subset=dedup)
    return df.sort_values(sort_by).reset_index(drop=True)

# Per-category dedup/sort config
CAT_CONFIG = {
    "app_wise":  (["month_year","app_name"], ["month_year","app_name"], ["month_year","app_name"]),
    "statewise": (["month_year","state"],    ["month_year","state"],    ["month_year","state"]),
    "aggregate": (["month_year"],            ["month_year"],            ["month_year"]),
    "mandate":   (["month_year","remitter_bank"], ["month_year","remitter_bank"], ["month_year","remitter_bank"]),
}

# SQL generation
def generate_sql():
    out_dir = str(OUTPUT_DIR.resolve()).replace("\\", "/")
    sql = f"""\
-- ============================================================
-- NPCI UPI Data — MySQL Import Script
-- Output dir: {out_dir}
-- Usage: mysql --local-infile=1 -u <user> -p <db> < mysql_import.sql
-- ============================================================
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- upi_aggregate
-- UPI tab on product-statistics; data from FY 2016-17 onwards
DROP TABLE IF EXISTS `upi_aggregate`;
CREATE TABLE `upi_aggregate` (
  `id`           INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `month_year`   CHAR(7)       NOT NULL  COMMENT 'YYYY-MM',
  `banks_on_upi` SMALLINT UNSIGNED  NULL  COMMENT 'No. of banks live on UPI',
  `volume_mn`    DECIMAL(18,4)     NULL  COMMENT 'Volume in millions',
  `value_cr`     DECIMAL(18,4)     NULL  COMMENT 'Value in crores INR',
  `created_at`   TIMESTAMP     NOT NULL  DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agg_my` (`month_year`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

LOAD DATA LOCAL INFILE '{out_dir}/upi_aggregate.csv'
  INTO TABLE `upi_aggregate`
  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
  LINES TERMINATED BY '\\n' IGNORE 1 ROWS
  (`month_year`,`banks_on_upi`,`volume_mn`,`value_cr`);

-- upi_apps
DROP TABLE IF EXISTS `upi_apps`;
CREATE TABLE `upi_apps` (
  `id`              INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `month_year`      CHAR(7)       NOT NULL  COMMENT 'YYYY-MM',
  `app_name`        VARCHAR(255)  NOT NULL,
  `cit_volume_mn`   DECIMAL(18,4)     NULL  COMMENT 'Customer Initiated Transactions volume',
  `cit_value_cr`    DECIMAL(18,4)     NULL  COMMENT 'Customer Initiated Transactions value',
  `b2c_volume_mn`   DECIMAL(18,4)     NULL,
  `b2c_value_cr`    DECIMAL(18,4)     NULL,
  `b2b_volume_mn`   DECIMAL(18,4)     NULL,
  `b2b_value_cr`    DECIMAL(18,4)     NULL,
  `total_volume_mn` DECIMAL(18,4)     NULL,
  `total_value_cr`  DECIMAL(18,4)     NULL,
  `created_at`      TIMESTAMP     NOT NULL  DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_apps_my_app` (`month_year`,`app_name`(100))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

LOAD DATA LOCAL INFILE '{out_dir}/upi_apps.csv'
  INTO TABLE `upi_apps`
  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
  LINES TERMINATED BY '\\n' IGNORE 1 ROWS
  (`month_year`,`app_name`,`cit_volume_mn`,`cit_value_cr`,
   `b2c_volume_mn`,`b2c_value_cr`,`b2b_volume_mn`,`b2b_value_cr`,
   `total_volume_mn`,`total_value_cr`);

-- upi_mandate
-- Bank-wise IPO mandate execution. Data only exists for May (IPO season).
-- Fields: approved%, bd% (bank declined), td% (technical decline)
DROP TABLE IF EXISTS `upi_mandate`;
CREATE TABLE `upi_mandate` (
  `id`            INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `month_year`    CHAR(7)       NOT NULL  COMMENT 'YYYY-MM',
  `remitter_bank` VARCHAR(255)  NOT NULL,
  `total_volume`  DECIMAL(18,2)     NULL  COMMENT 'IPO mandate execution count',
  `approved_pct`  DECIMAL(6,2)      NULL  COMMENT 'Approved %',
  `bd_pct`        DECIMAL(6,2)      NULL  COMMENT 'Bank Declined %',
  `td_pct`        DECIMAL(6,2)      NULL  COMMENT 'Technical Decline %',
  `created_at`    TIMESTAMP     NOT NULL  DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_mandate_my_bank` (`month_year`,`remitter_bank`(100))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

LOAD DATA LOCAL INFILE '{out_dir}/upi_mandate.csv'
  INTO TABLE `upi_mandate`
  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
  LINES TERMINATED BY '\\n' IGNORE 1 ROWS
  (`month_year`,`remitter_bank`,`total_volume`,`approved_pct`,`bd_pct`,`td_pct`);

-- upi_statewise
DROP TABLE IF EXISTS `upi_statewise`;
CREATE TABLE `upi_statewise` (
  `id`         INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  `month_year` CHAR(7)       NOT NULL  COMMENT 'YYYY-MM',
  `state`      VARCHAR(100)  NOT NULL,
  `volume_mn`  DECIMAL(18,4)     NULL,
  `value_cr`   DECIMAL(18,4)     NULL,
  `created_at` TIMESTAMP     NOT NULL  DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sw_my_st` (`month_year`,`state`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

LOAD DATA LOCAL INFILE '{out_dir}/upi_statewise.csv'
  INTO TABLE `upi_statewise`
  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
  LINES TERMINATED BY '\\n' IGNORE 1 ROWS
  (`month_year`,`state`,`volume_mn`,`value_cr`);

SET FOREIGN_KEY_CHECKS = 1;
"""
    (BASE_DIR / "mysql_import.sql").write_text(sql, encoding="utf-8")

# Main
def main():
    all_frames: Dict[str, List[pd.DataFrame]] = {
        "app_wise": [], "statewise": [], "aggregate": [], "mandate": []
    }
    discovered: Dict[str, Optional[str]] = {}   # category → confirmed tab_name

    for cfg in TAB_CONFIGS:
        category  = cfg["category"]
        api_url   = cfg["api"]
        try_names = cfg["try_names"]
        csv_name  = cfg["csv"]

        # Skip if we already have data for this category (don't double-count)
        if all_frames[category]:
            continue

        print(f"\n{'='*60}")
        print(f"CATEGORY: {category.upper()}  |  csv: {csv_name}")
        print("="*60)

        # Discover which tab_name works
        disc_key = f"{category}::{api_url}"
        if disc_key not in discovered:
            print(f"  Discovering tab name from: {try_names}")
            discovered[disc_key] = discover_tab_name(cfg)
        tab_name = discovered[disc_key]

        if tab_name is None:
            print(f"  [SKIP] None of {try_names} returned data")
            continue

        mode       = cfg.get("mode", "ecosystem")
        excel_type = cfg.get("excel_type", "monthly")
        row_builder = ROW_BUILDER[category]
        frames: List[pd.DataFrame] = []
        total_rows  = 0

        if mode == "product_stat":
            # Iterate financial years
            for fy in reversed(FY_RANGES):
                records = fetch_product_stat_pages(tab_name, fy, excel_type, category)
                if not records:
                    continue
                fy_rows = []
                for r in records:
                    row = row_builder(r, "")   # month_year comes from record itself
                    if row:
                        fy_rows.append(row)
                print(f"  FY {fy}  {len(records):4d} records -> {len(fy_rows)} rows")
                if fy_rows:
                    frames.append(pd.DataFrame(fy_rows))
                    total_rows += len(fy_rows)

        elif mode == "product_stat_monthly":
            # product-statistic API with month/year params
            for year in range(CURRENT_YEAR, START_YEAR - 1, -1):
                year_rows = []
                for month_abbr, month_num in MONTHS:
                    if year > CURRENT_YEAR or (year == CURRENT_YEAR and month_num > CURRENT_MONTH):
                        continue
                    month_year = f"{year}-{month_num:02d}"
                    records = fetch_product_stat_monthly(tab_name, year, month_abbr, category)
                    if not records:
                        continue
                    for r in records:
                        row = row_builder(r, month_year)
                        if row:
                            year_rows.append(row)
                    print(f"  {month_year}  {len(records):4d} records -> {len(year_rows)} rows so far")
                if year_rows:
                    frames.append(pd.DataFrame(year_rows))
                    total_rows += len(year_rows)

        elif mode == "files_api":
            # products-statistics-files: iterate calendar year × month
            for year in range(CURRENT_YEAR, START_YEAR - 1, -1):
                year_rows = []
                for month_abbr, month_num in MONTHS:
                    if year > CURRENT_YEAR or (year == CURRENT_YEAR and month_num > CURRENT_MONTH):
                        continue
                    month_year = f"{year}-{month_num:02d}"
                    records = fetch_files_api(tab_name, year, month_abbr, category)
                    if not records:
                        continue
                    for r in records:
                        row = row_builder(r, month_year)
                        if row:
                            year_rows.append(row)
                    print(f"  {month_year}  {len(records):4d} records -> {len(year_rows)} rows so far")
                if year_rows:
                    frames.append(pd.DataFrame(year_rows))
                    total_rows += len(year_rows)

        else:
            # ecosystem: iterate calendar year × month
            excel_fallback = cfg.get("excel_fallback", False)
            for year in range(CURRENT_YEAR, START_YEAR - 1, -1):
                year_rows = []
                for month_abbr, month_num in MONTHS:
                    if year > CURRENT_YEAR or (year == CURRENT_YEAR and month_num > CURRENT_MONTH):
                        continue
                    month_year = f"{year}-{month_num:02d}"
                    records = fetch_all_pages(api_url, tab_name, year, month_abbr, category=category)

                    # Excel fallback: if results empty, get fileUrl from response and parse Excel
                    if not records and excel_fallback:
                        resp = fetch_api(api_url, tab_name, year, month_abbr, category=category)
                        if resp:
                            file_url = _extract_file_url(resp)
                            if file_url:
                                cat_dir = RAW_DIR / category
                                xlsx_path = cat_dir / f"{tab_name}__{year}_{month_abbr}.xlsx"
                                if _download_excel(file_url, xlsx_path):
                                    records = _parse_excel(xlsx_path)

                    if not records:
                        continue
                    for r in records:
                        row = row_builder(r, month_year)
                        if row:
                            year_rows.append(row)
                    print(f"  {month_year}  {len(records):4d} records -> {len(year_rows)} rows so far")
                if year_rows:
                    frames.append(pd.DataFrame(year_rows))
                    total_rows += len(year_rows)

        print(f"\n  Total raw rows for {category}: {total_rows}")
        all_frames[category] = frames

    # Build and save CSVs
    CSV_NAMES = {
        "aggregate": "upi_aggregate.csv",
        "app_wise":  "upi_apps.csv",
        "mandate":   "upi_mandate.csv",
        "statewise": "upi_statewise.csv",
    }

    results: Dict[str, pd.DataFrame] = {}

    print(f"\n{'='*60}")
    print("BUILDING CSVs")
    print("="*60)

    for cat, csv_name in CSV_NAMES.items():
        drop_na, dedup, sort_by = CAT_CONFIG[cat]
        df = finalize(all_frames[cat], drop_na=drop_na, dedup=dedup, sort_by=sort_by)
        results[cat] = df

        if not df.empty:
            out = OUTPUT_DIR / csv_name
            df.to_csv(out, index=False)
            dates = df["month_year"].dropna().sort_values()
            print(f"  {csv_name:30s}  {len(df):6d} rows  {dates.iloc[0]} -> {dates.iloc[-1]}")
        else:
            print(f"  {csv_name:30s}  NO DATA")

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print("="*60)
    for cat, csv_name in CSV_NAMES.items():
        df = results.get(cat, pd.DataFrame())
        if df.empty:
            print(f"  {csv_name:30s}  NO DATA")
        else:
            dates = df["month_year"].dropna().sort_values()
            print(f"  {csv_name:30s}  {len(df):6d} rows  |  {dates.iloc[0]} -> {dates.iloc[-1]}")

    failed = [l for l in FAILED_LOG.read_text(encoding="utf-8").strip().splitlines() if l]
    errors = [l for l in ERRORS_LOG.read_text(encoding="utf-8").strip().splitlines() if l]
    if failed: print(f"\n  API failures : {len(failed)} — see failed_downloads.txt")
    if errors:  print(f"  Parse errors : {len(errors)} — see parse_errors.txt")

    generate_sql()
    print(f"\n[DONE] mysql_import.sql written.")


if __name__ == "__main__":
    main()
