import re
import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from datetime import datetime
import sys
import pandas as pd
import requests

from openai import OpenAI
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from newsapi import NewsApiClient

if load_dotenv is not None:
    load_dotenv()

INPUT_COLUMNS = ("name", "email", "company", "address", "city", "state", "zip", "country")
DEFAULT_INPUT_PATH = Path("test_data/leads_test_with_zip.csv")
DEFAULT_OUTPUT_PATH = Path("output/leads_enriched.json")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")

def load_input_csv(input_path):
    leads = pd.read_csv(input_path, dtype=str, encoding="utf-8-sig")
    leads.columns = [column.strip() for column in leads.columns]
    if tuple(leads.columns) != INPUT_COLUMNS:
        raise ValueError(f"Expected CSV header: {','.join(INPUT_COLUMNS)}")
    return leads.fillna("")

def process_row(row):
    return {column: row[column] for column in INPUT_COLUMNS}

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to your environment or .env file."
        )
    return OpenAI(api_key=api_key)


@lru_cache(maxsize=1)
def get_news_client():
    if NewsApiClient is None:
        logger.warning("newsapi-python is not installed; skipping news enrichment")
        return None
    if not NEWS_API_KEY:
        logger.warning("NEWSAPI_KEY is not configured; skipping news enrichment")
        return None
    return NewsApiClient(api_key=NEWS_API_KEY)


def safe_call(func, *args, default=None, **kwargs):
    """
    Wrap a function call and return a default value on failure.
    This keeps a single API failure from aborting lead enrichment.
    """
    try:
        return func(*args, **kwargs)
    except requests.HTTPError as e:
        logger.warning(f"{func.__name__} HTTP error: {e}")
    except requests.Timeout:
        logger.warning(f"{func.__name__} timed out")
    except requests.ConnectionError:
        logger.warning(f"{func.__name__} connection error")
    except Exception as e:
        logger.warning(f"{func.__name__} unexpected error: {e}")
    return default

# ---------------------------------
def get_geo_key(zip_code=None, city=None, state=None,
                zip_lookup=None, place_lookup=None):

    if zip_code and zip_lookup:
        zip_key = zip_lookup.get(str(zip_code).strip())
        if zip_key:
            return "Zip", zip_key

    if city and state and place_lookup:
        full_name = f"{city}, {state}".strip().lower()
        place_key = place_lookup.get(full_name)
        if place_key:
            return "Place", place_key

    if city and place_lookup:
        place_key = place_lookup.get(city.strip().lower())
        if place_key:
            return "Place", place_key

    return None, None

def fetch_members(cube, level):
    url = "https://api.datausa.io/tesseract/members"
    resp = requests.get(url, params={"cube": cube, "level": level}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def normalize_city_name(name: str) -> str:
    return name.strip().lower()

def build_place_lookup(members):
    rows = members["members"]
    lookup = {}

    for m in rows:
        key = m.get("key")
        caption = m.get("caption")

        if key and caption:
            lookup[normalize_city_name(caption)] = key

    return lookup
def build_zip_lookup(members):
    rows = members["members"]
    lookup = {}

    for m in rows:
        key = m.get("key")
        caption = m.get("caption")

        if key and caption:
            lookup[caption.strip()] = key

    return lookup

def query_datausa_median_income(
    year=2022,
    zip_code=None,
    city=None,
    state=None,
    zip_lookup=None,
    place_lookup=None,
):
    """
    Query median household income for the total population (Race=Total).
    """
    level, geo_key = get_geo_key(
        zip_code=zip_code,
        city=city,
        state=state,
        zip_lookup=zip_lookup,
        place_lookup=place_lookup,
    )
    if not level or not geo_key:
        return None

    resp = requests.get(
        "https://api.datausa.io/tesseract/data.jsonrecords",
        params={
            "cube": "acs_ygr_median_household_income_race_5",
            "drilldowns": f"{level},Year,Race",
            "measures": "Household Income by Race",
            "include": f"Year:{year};{level}:{geo_key};Race:0",  # Race:0 = Total
            "limit": "100,0",
        },
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json().get("data", [])
    if not rows:
        return None

    row = rows[0]
    return {
        "level_used": level,
        "geo_key": geo_key,
        "year": row.get("Year"),
        "median_income": row.get("Household Income by Race"),
    }

def query_datausa_metric(
    cube,
    measure,
    year=2022,
    zip_code=None,
    city=None,
    state=None,
    zip_lookup=None,
    place_lookup=None,
):
    level, geo_key = get_geo_key(
        zip_code=zip_code,
        city=city,
        state=state,
        zip_lookup=zip_lookup,
        place_lookup=place_lookup,
    )

    if not level or not geo_key:
        return None

    url = "https://api.datausa.io/tesseract/data.jsonrecords"
    params = {
        "cube": cube,
        "drilldowns": f"{level},Year",
        "measures": measure,
        "include": f"Year:{year};{level}:{geo_key}",
        "limit": "100,0",
    }

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()

    data = resp.json()
    rows = data.get("data", [])
    if not rows:
        return None

    row = rows[0]

    return {
        "level_used": level,
        "geo_key": geo_key,
        "year": row.get("Year"),
        "value": row.get(measure),
        "place": row.get("Place"),
        "zip": row.get("Zip"),
        "raw_row": row,
    }

def query_datausa_tenure(
    year=2022,
    zip_code=None,
    city=None,
    state=None,
    zip_lookup=None,
    place_lookup=None,
):
    """
    Query the tenure cube and return owner/renter households and renter rate.
    """
    level, geo_key = get_geo_key(
        zip_code=zip_code,
        city=city,
        state=state,
        zip_lookup=zip_lookup,
        place_lookup=place_lookup,
    )

    if not level or not geo_key:
        return None

    url = "https://api.datausa.io/tesseract/data.jsonrecords"
    params = {
        "cube": "acs_ygo_tenure_5",
        "drilldowns": f"{level},Year,Occupied By",
        "measures": "Household Ownership",
        "include": f"Year:{year};{level}:{geo_key}",
        "limit": "100,0",
    }

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()

    rows = resp.json().get("data", [])
    if not rows:
        return None

    # The response should contain one row for owners and one for renters.
    owner_households = 0
    renter_households = 0
    for row in rows:
        occ_type = row.get("Occupied By")
        count = row.get("Household Ownership", 0)
        if occ_type == "Owner Occupied":
            owner_households = count
        elif occ_type == "Renter Occupied":
            renter_households = count

    total = owner_households + renter_households
    renter_rate = (renter_households / total) if total else None

    return {
        "level_used": level,
        "geo_key": geo_key,
        "year": rows[0].get("Year"),
        "owner_households": owner_households,
        "renter_households": renter_households,
        "total_households": total,
        "renter_rate": renter_rate,
    }

DATAUSA_METRICS = {
    "population": {
        "cube": "acs_yg_total_population_5",
        "measure": "Population",
    },
    "housing_median_value": {
        "cube": "acs_yg_housing_median_value_5",
        "measure": "Property Value",
    },
    "tenure": {
        "cube": "acs_ygo_tenure_5",
        "measure": "Household Ownership",
        "extra_drilldown": "Occupied By",
    },
}

def get_datausa_metric(
    metric_name,
    year=2024,
    zip_code=None,
    city=None,
    state=None,
    lookups=None,
):
    config = DATAUSA_METRICS[metric_name]
    cube = config["cube"]
    measure = config["measure"]

    zip_lookup = lookups[cube]["zip"]
    place_lookup = lookups[cube]["place"]

    return query_datausa_metric(
        cube=cube,
        measure=measure,
        year=year,
        zip_code=zip_code,
        city=city,
        state=state,
        zip_lookup=zip_lookup,
        place_lookup=place_lookup,
    )

def build_cube_lookups(cube):
    place_members = fetch_members(cube, "Place")
    zip_members = fetch_members(cube, "Zip")

    return {
        "place": build_place_lookup(place_members),
        "zip": build_zip_lookup(zip_members),
    }

@lru_cache(maxsize=1)
def get_lookups():
    lookup_map = {}
    for cube in [
        "acs_yg_total_population_5",
        "acs_yg_housing_median_value_5",
        "acs_ygo_tenure_5",
        "acs_ygr_median_household_income_race_5"
    ]:
        lookup_map[cube] = safe_call(
            build_cube_lookups,
            cube,
            default={"place": {}, "zip": {}},
        )
    return lookup_map

# use new API to find relevant news of the given location
def get_news(city: str, company: str):
    news_client = get_news_client()
    if news_client is None:
        return {"city_links": [], "company_links": []}

    city_query = " ".join(part for part in [city, "housing"] if part) or "housing"
    company_query = company or city_query

    city_articles = news_client.get_everything(
        q=city_query,
        language="en",
        sort_by="relevancy",
        page_size=5
    )
    company_articles = news_client.get_everything(
        q=company_query,
        language="en",
        sort_by="relevancy",
        page_size=5
    )
    return {
        "city_links": [
            article["url"]
            for article in city_articles.get("articles", [])
            if article.get("url")
        ],
        "company_links": [
            article["url"]
            for article in company_articles.get("articles", [])
            if article.get("url")
        ],
    }

LARGE_COMPANY_KEYWORDS = [
    "billion", "largest", "international", "global",
    "fortune", "publicly traded", "REIT",
]
MEDIUM_COMPANY_KEYWORDS = [
    "million", "regional", "headquartered", "operates in",
]

def classify_company_size(extract: str) -> str:
    """
    Return one of: 'large', 'medium', or 'small_or_unknown'.
    """
    if not extract:
        return "small_or_unknown"
    text = extract.lower()
    if any(kw in text for kw in LARGE_COMPANY_KEYWORDS):
        return "large"
    if any(kw in text for kw in MEDIUM_COMPANY_KEYWORDS):
        return "medium"
    return "small_or_unknown"

# -----------------------------------------------
HEADERS = {
    "User-Agent": "EliseAI-LeadEnrichment/1.0 (https://github.com/ShanglinZeng/eliseai; tonyzengshanglin@gmail.com)"
}

def search_wikipedia(company: str):
    resp = requests.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "opensearch",
            "search": company,
            "limit": 3,
            "format": "json",
        },
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    titles = data[1] if len(data) > 1 else []
    return titles[0] if titles else None


def fetch_wikipedia_summary(title: str):
    title_url = title.replace(" ", "_")
    resp = requests.get(
        f"https://en.wikipedia.org/api/rest_v1/page/summary/{title_url}",
        headers=HEADERS,
        timeout=10,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()

PROPERTY_KEYWORDS = [
    "property management", "real estate", "multifamily", "apartment",
    "rental", "REIT", "housing", "residential",
]

def enrich_with_wikipedia(company: str) -> dict:
    result = {
        "wiki_found": False,
        "title": None,
        "extract": None,
        "is_property_related": False,
        "company_size_tier": "unknown",
        "url": None,
    }
    
    if not company:
        return result
    
    title = search_wikipedia(company)
    if not title:
        return result
    
    summary = fetch_wikipedia_summary(title)
    if not summary or "extract" not in summary:
        return result
    
    extract = summary["extract"]
    is_property = any(kw.lower() in extract.lower() for kw in PROPERTY_KEYWORDS)
    
    result["wiki_found"] = True
    result["title"] = summary.get("title")
    result["extract"] = extract
    result["url"] = summary.get("content_urls", {}).get("desktop", {}).get("page")
    result["is_property_related"] = is_property
    
    # Only classify company size when the company is clearly property-related.
    if is_property:
        result["company_size_tier"] = classify_company_size(extract)
    
    return result

def enrich_lead_full(lead: dict) -> dict:
    """
    Return the full enrichment payload for a single lead.
    Any individual API failure is handled without aborting the rest.
    """
    city = lead.get("city")
    state = lead.get("state")
    company = lead.get("company")
    zip_code = lead.get("zip")
    lookups = get_lookups()
    income_lookups = lookups["acs_ygr_median_household_income_race_5"]
    # Each DataUSA call is isolated so one failure does not break enrichment.
    datausa = {
        "population": safe_call(
            get_datausa_metric, "population",
            zip_code=zip_code, city=city, state=state, lookups=lookups,
        ),
        "median_income": safe_call(
            query_datausa_median_income,
            year=2022, zip_code=zip_code, city=city, state=state,
            zip_lookup=income_lookups["zip"],
            place_lookup=income_lookups["place"],
        ),
        "housing_median_value": safe_call(
            get_datausa_metric, "housing_median_value",
            zip_code=zip_code, city=city, state=state, lookups=lookups,
        ),
        "tenure": safe_call(
            query_datausa_tenure,
            year=2022, zip_code=zip_code, city=city, state=state,
            zip_lookup=lookups["acs_ygo_tenure_5"]["zip"],
            place_lookup=lookups["acs_ygo_tenure_5"]["place"],
        ),
    }
    
    # NewsAPI
    news = safe_call(get_news, city, company, default={"city_links": [], "company_links": []})
    
    # Wikipedia
    wiki = safe_call(
        enrich_with_wikipedia, company,
        default={
            "wiki_found": False, "title": None, "extract": None,
            "is_property_related": False, "company_size_tier": "unknown", "url": None,
        },
    )
    
    return {
        "lead": lead,
        "datausa": datausa,
        "news": news,
        "wiki": wiki,
    }

# scoring

PROPERTY_KEYWORDS = ["properties", "property", "management", "realty", 
                     "residential", "apartment", "housing", "communities"]

PERSONAL_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com"}

MAJOR_STATES = {"CA", "TX", "NY", "FL", "WA", "MA", "VA", "CO", "IL", "GA", "NC", "PA", "AZ"}


def score_lead(enriched: dict) -> dict:
    """
    Input: output from enrich_lead_full()
    Output: scoring result dict
    """
    lead = enriched["lead"]
    datausa = enriched.get("datausa") or {}
    wiki = enriched.get("wiki") or {}
    news = enriched.get("news") or {}
    
    score = 0
    notes = []
    disqualifiers = []
    
    # ---- Hard disqualifiers ----
    email = (lead.get("email") or "").lower()
    email_domain = email.split("@")[-1] if "@" in email else ""
    
    if email_domain in PERSONAL_EMAIL_DOMAINS:
        disqualifiers.append("Personal email domain (not a B2B prospect)")
    
    if (lead.get("country") or "").upper() not in ("US", "USA", "UNITED STATES"):
        disqualifiers.append("Outside US — EliseAI's primary market")
    
    if wiki.get("wiki_found") and not wiki.get("is_property_related"):
        disqualifiers.append(f"Company '{wiki.get('title')}' is not property-related per Wikipedia")
    
    # ---- 1. ICP Fit (40 points) ----
    icp = 0
    company = (lead.get("company") or "").lower()
    
    if email_domain and email_domain not in PERSONAL_EMAIL_DOMAINS:
        icp += 10
        notes.append(f"Corporate email domain ({email_domain})")
    
    if any(kw in company for kw in PROPERTY_KEYWORDS):
        icp += 10
        notes.append(f"Company name suggests property/real estate business")
    
    size_tier = wiki.get("company_size_tier", "unknown")
    if wiki.get("is_property_related"):
        if size_tier == "large":
            icp += 20
            notes.append(f"Wikipedia: large property company ({wiki.get('title')})")
        elif size_tier == "medium":
            icp += 14
            notes.append(f"Wikipedia: mid-size property company")
        else:
            icp += 8
            notes.append(f"Wikipedia confirms property-related business")
    elif not wiki.get("wiki_found"):
        icp += 5
    
    icp = min(icp, 40)
    score += icp
    
    # ---- 2. Market Signal (30 points) ----
    market = 0
    
    tenure = datausa.get("tenure") or {}
    renter_rate = tenure.get("renter_rate")
    if renter_rate is not None:
        if renter_rate >= 0.50:
            market += 12
            notes.append(f"High renter market: {renter_rate:.0%}")
        elif renter_rate >= 0.35:
            market += 8
            notes.append(f"Moderate renter market: {renter_rate:.0%}")
        else:
            market += 3
    
    pop_obj = datausa.get("population") or {}
    pop = pop_obj.get("value")
    if pop:
        if pop >= 500_000:
            market += 10
            notes.append(f"Large market: {int(pop):,} residents")
        elif pop >= 100_000:
            market += 7
            notes.append(f"Mid-size market: {int(pop):,} residents")
        else:
            market += 4
    
    income_obj = datausa.get("median_income") or {}
    income = income_obj.get("median_income")
    if income:
        if 50_000 <= income <= 110_000:
            market += 8
            notes.append(f"Median income ${int(income):,} — strong rental affordability")
        elif income > 110_000:
            market += 5
            notes.append(f"Premium market: median income ${int(income):,}")
        else:
            market += 2
    
    market = min(market, 30)
    score += market
    
    # ---- 3. Engagement Readiness (20 points) ----
    eng = 0
    company_links = news.get("company_links") or []
    city_links = news.get("city_links") or []
    
    if len(company_links) >= 3:
        eng += 10
        notes.append(f"{len(company_links)} recent company news mentions")
    elif len(company_links) >= 1:
        eng += 5
        notes.append(f"{len(company_links)} company news mention(s)")
    
    if len(city_links) >= 3:
        eng += 10
        notes.append(f"Active local housing news ({len(city_links)} articles)")
    elif len(city_links) >= 1:
        eng += 5
    
    eng = min(eng, 20)
    score += eng
    
    # ---- 4. Geography (10 points) ----
    geo = 0
    if (lead.get("country") or "").upper() in ("US", "USA", "UNITED STATES"):
        geo += 5
    state = (lead.get("state") or "").upper()
    if state in MAJOR_STATES:
        geo += 5
        notes.append(f"{state} — major EliseAI market")
    
    score += geo
    
    # ---- Apply disqualifier caps ----
    if any("Personal email" in d or "Outside US" in d for d in disqualifiers):
        score = min(score, 40)
    elif disqualifiers:
        score = min(score, 50)
    
    # ---- Tier ----
    if score >= 75:
        tier = "A"
    elif score >= 50:
        tier = "B"
    else:
        tier = "C"
    
    return {
        "score": score,
        "tier": tier,
        "breakdown": {"icp": icp, "market": market, "engagement": eng, "geography": geo},
        "notes": notes,
        "disqualifiers": disqualifiers,
    }

# draft email
def draft_email(enriched: dict) -> str | None:
    """
    Generate a personalized cold email using enriched data + scoring notes.
    Returns None for Tier C leads (use generic nurture sequence instead).
    """
    lead = enriched["lead"]
    score = enriched["score"]
    wiki_extract = (enriched.get("wiki") or {}).get("extract") or ""
    
    if score["tier"] == "C":
        return None
    
    notes_block = "\n".join(f"- {n}" for n in score["notes"])
    
    prompt = f"""You are an SDR at EliseAI, an AI assistant that automates leasing, \
renewals, and resident communication for multifamily property management companies. \
Customers include Greystar, GoldOller, Cardinal Group, AvalonBay, and Dominium.

Write a personalized cold email to this lead.

LEAD:
- Name: {lead['name']}
- Company: {lead['company']}
- Location: {lead['city']}, {lead['state']}

ENRICHED INSIGHTS (pick the single sharpest one as your hook):
{notes_block}

COMPANY BACKGROUND (from Wikipedia, may be empty):
{wiki_extract or '(no Wikipedia info available)'}

REQUIREMENTS:
- Maximum 80 words, 3-4 sentences
- Reference ONE specific data point from the insights as the hook
- Translate that data point into an implied operational pain (e.g. "high renter rate \
usually means the leasing team drowns in after-hours inquiries")
- Do NOT pitch features. Mention EliseAI in one sentence as the role we play.
- End with a soft CTA like "worth a quick chat?" — never "book a 30-min call"
- Avoid these phrases: "I hope this finds you well", "I came across", "I wanted to reach out"
- Output ONLY the email body. No subject line, no signature, no preamble."""

    response = get_openai_client().chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()



def make_run_dir(base="output") -> Path:
    """Create a timestamped run directory and return its path."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = Path(base) / f"run_{timestamp}"
    (run_dir / "insights").mkdir(parents=True, exist_ok=True)
    return run_dir


def slugify(text: str) -> str:
    """Convert company name to safe filename."""
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_-]+", "_", text).strip("_")
    return text or "unknown"


def write_insight_file(run_dir: Path, idx: int, enriched: dict) -> Path | None:
    """Write a single lead's email to a .txt file. Returns path or None."""
    email_body = enriched.get("email")
    lead = enriched["lead"]
    score = enriched["score"]
    
    if not email_body:
        return None
    
    company_slug = slugify(lead.get("company", "unknown"))
    filename = f"{idx:02d}_{company_slug}_Tier{score['tier']}.txt"
    filepath = run_dir / "insights" / filename
    
    content = f"""TO:      {lead['name']} <{lead['email']}>
COMPANY: {lead['company']}
TIER:    {score['tier']} ({score['score']}/100)
LOCATION: {lead['city']}, {lead['state']}

KEY INSIGHTS:
{chr(10).join('- ' + n for n in score['notes'])}

{'=' * 60}

{email_body}
"""
    filepath.write_text(content, encoding="utf-8")
    return filepath


def write_summary_csv(run_dir: Path, results: list[dict]) -> Path:
    """Write a triage-friendly summary CSV for SDR."""
    import csv
    filepath = run_dir / "summary.csv"
    with filepath.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "tier", "score", "name", "company", "city", "state",
            "email", "has_draft", "top_insight"
        ])
        sorted_results = sorted(
            results,
            key=lambda r: (r["score"]["tier"], -r["score"]["score"]),
        )
        for r in sorted_results:
            lead = r["lead"]
            score = r["score"]
            top_insight = score["notes"][0] if score["notes"] else ""
            writer.writerow([
                score["tier"], score["score"],
                lead["name"], lead["company"],
                lead["city"], lead["state"], lead["email"],
                "yes" if r.get("email") else "no",
                top_insight,
            ])
    return filepath

def debug_one_lead():
    test_lead = {
        "name": "John Smith",
        "email": "john.smith@greystar.com",
        "company": "Greystar",
        "address": "750 Bering Dr",
        "city": "Houston",
        "state": "TX",
        "zip": "77057",
        "country": "USA",
    }

    enriched = enrich_lead_full(test_lead)
    result = score_lead(enriched)

    print("Score:", result["score"])
    print("Tier:", result["tier"])
    print("Breakdown:", result["breakdown"])
    print("Notes:")
    for n in result["notes"]:
        print(" -", n)
    print("Disqualifiers:", result.get("disqualifiers"))
    print()
    print("News company_links count:", len(enriched["news"]["company_links"]))
    print("News city_links count:", len(enriched["news"]["city_links"]))

def run_pipeline_on_file(input_path: str):
    """Run the full pipeline on a given CSV. Reusable as a library function."""
    run_dir = make_run_dir()
    print(f"Run directory: {run_dir}\n")

    leads_df = load_input_csv(input_path)
    results = []
    for idx, row in leads_df.iterrows():
        lead = process_row(row)
        print(f"[{idx+1}/{len(leads_df)}] {lead['company']}...")
        enriched = enrich_lead_full(lead)
        scored = score_lead(enriched)
        enriched["score"] = scored
        email = safe_call(draft_email, enriched, default=None)
        enriched["email"] = email
        write_insight_file(run_dir, idx + 1, enriched)
        print(f"  -> Tier {scored['tier']} ({scored['score']}/100)")
        results.append(enriched)

    json_path = run_dir / "enriched_leads.json"
    json_results = [
        {k: v for k, v in r.items() if k != "email"}
        for r in results
    ]
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(json_results, f, indent=2, default=str)
    write_summary_csv(run_dir, results)
    return run_dir

def main():
    input_path = sys.argv[1] if len(sys.argv) > 1 else "leads_test_with_zip.csv"
    run_pipeline_on_file(input_path)


if __name__ == "__main__":
    main()
