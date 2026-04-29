# EliseAI Lead Enrichment Pipeline

This project processes property-management lead CSVs and turns them into:

- enriched lead records
- scored lead tiers
- SDR-ready insight files
- a summary CSV for triage

The pipeline combines public data sources with LLM-generated outreach drafts.

## What It Does

For each lead, the pipeline:

1. Loads a CSV row with contact and building context.
2. Enriches the lead with:
   - DataUSA market data
   - NewsAPI company and city news links
   - Wikipedia company context
3. Scores the lead across:
   - ICP fit
   - market signals
   - engagement readiness
   - geography
4. Generates a short outreach email for Tier `A` and `B` leads.
5. Writes run artifacts to a timestamped output folder.

## Project Files

- [process_lead.py](/Users/cengshanglin/Desktop/EliseAI/process_lead.py): main enrichment, scoring, and draft-generation pipeline
- [watcher.py](/Users/cengshanglin/Desktop/EliseAI/watcher.py): watches the `inputs/` folder and auto-runs the pipeline for new CSVs
- [test_data/leads_test_with_zip.csv](/Users/cengshanglin/Desktop/EliseAI/test_data/leads_test_with_zip.csv): sample input file
- [output/](/Users/cengshanglin/Desktop/EliseAI/output): generated run outputs

## Requirements

Python 3.10+ is recommended.

Install dependencies:

```bash
pip install pandas requests openai python-dotenv newsapi-python watchdog
```

## Environment Variables

Create a `.env` file in the project root:

```env
OPENAI_API_KEY=your_openai_api_key
NEWS_API_KEY=your_newsapi_key
```

Notes:

- `OPENAI_API_KEY` is required for email draft generation.
- `NEWS_API_KEY` is optional, but without it news enrichment will be empty.
- If `python-dotenv` is installed, `process_lead.py` will load `.env` automatically.

## Input Format

The pipeline expects a CSV with this exact header:

```csv
name,email,company,address,city,state,zip,country
```

Example:

```csv
John Smith,john.smith@greystar.com,Greystar,750 Bering Dr,Houston,TX,77057,USA
```

## Run the Pipeline

Run against a CSV file:

```bash
python3 process_lead.py test_data/leads_test_with_zip.csv
```

If no path is provided, `process_lead.py` falls back to its internal default input path.

## Output Structure

Each run creates a timestamped directory under `output/`:

```text
output/
  run_YYYY-MM-DD_HH-MM-SS/
    enriched_leads.json
    summary.csv
    insights/
      01_greystar_TierA.txt
      02_goldoller_real_estate_TierB.txt
```

Generated files:

- `enriched_leads.json`: full enrichment and scoring results, excluding the email body
- `summary.csv`: SDR-friendly triage table
- `insights/*.txt`: per-lead insight file with the score, top notes, and generated outreach draft

## Lead Scoring

The current scoring model uses:

- `ICP fit`: company relevance, business email, Wikipedia property relevance, company size hints
- `Market signal`: renter rate, population, median income
- `Engagement readiness`: recent company news and local housing news
- `Geography`: US focus and priority states

Disqualifiers can cap the final score, including:

- personal email domains
- non-US leads
- companies identified as unrelated to property management

Tier logic:

- `A`: score `>= 75`
- `B`: score `>= 50`
- `C`: score `< 50`

Tier `C` leads do not get a generated email draft.

## Watch Folder Mode

To automatically process incoming CSVs:

```bash
python3 watcher.py
```

Watcher behavior:

- watches `inputs/` for new or modified `.csv` files
- waits for file writes to finish before processing
- moves successful files to `inputs/processed/`
- moves failed files to `inputs/failed/`

## Data Sources

The pipeline currently uses:

- DataUSA for population, tenure, property value, and median income
- NewsAPI for city and company news links
- Wikipedia for company background and property-related classification
- OpenAI for email draft generation

## Known Limitations

- The scoring heuristics are simple and hand-tuned.
- Wikipedia-based company classification is heuristic and may miss smaller firms.
- News enrichment depends on a valid NewsAPI key and relevant search coverage.
- Email drafts are prompt-based and should be reviewed before sending.
- The repository currently contains historical output folders that may still use an older `emails/` subfolder name.

## Typical Workflow

1. Prepare a lead CSV with the required columns.
2. Set `OPENAI_API_KEY` and optionally `NEWS_API_KEY`.
3. Run `python3 process_lead.py <your_file.csv>`.
4. Review `summary.csv`.
5. Open the generated files in `insights/` for the best leads.

