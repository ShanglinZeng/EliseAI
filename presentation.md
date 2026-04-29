# EliseAI GTM Engineer
## Practical Assignment

**Shanglin Zeng**

Automating the inbound lead process

---

# The Pipeline

Per lead, four stages:

1. **Enrich** with public APIs
2. **Score** into Tier A / B / C
3. **Draft** a personalized email
4. **Output** copy-paste ready files

Triggered by dropping a CSV into `inputs/`.

---

# APIs Used

### DataUSA
Population · income · housing(renter/owner)

### Wikipedia
Company profile · property-related?

### NewsAPI
Company news · city housing news

---

# Why These Three

**Each API has a specific role**

DataUSA → market signal
Wikipedia → ICP fit
NewsAPI → engagement readiness

---


# Scoring Rubric — 100 pts

| Dimension | Weight |
|---|---|
| **ICP Fit** | 40 |
| **Market Signal** | 30 |
| **Engagement Readiness** | 20 |
| **Geographic Fit** | 10 |

Tier A: **75+** · Tier B: **50–74** · Tier C: **<50**

Hard cap: Gmail address → Tier C

---


# Three Key Assumptions

### 1. Good lead = large multifamily operator


### 2. Missing data ≠ negative signal


### 3. Wikipedia matches need a sanity check


---

# Email Drafting

GPT-4o with constrained prompt:

- One specific data point as the **hook**
- Translate data → implied operational pain
- One sentence on EliseAI's role
- Soft CTA, max 80 words

---

# How This Helps an SDR

**Three concrete time wins:**

| Task | Before | After |
|---|---|---|
| Prioritization | judgment overhead | sorted CSV |
| Research | 15–20 min/lead | <30 sec |
| Drafting | blank-page writing | editing |

**Augmentation, not replacement.**
SDR still owns the relationship.

---

# Rollout — 3 Phases / 10 Weeks

### Phase 1 (W1): Internal Validation

### Phase 2 (W2–6): Pilot

### Phase 3 (W7–10): Org-Wide


---

# Automation: File Watcher

**Trigger model — not just schedule**

`python watcher.py` watches `inputs/`

→ Drop any `.csv` → auto-process
→ Move to `processed/` on success
→ Move to `failed/` on error

Filename doesn't matter. Source doesn't matter.

---

# Live Demo

Drop CSV → watcher triggers → output ready


---
# Output Structure

```
output/run_2026-04-28_14-30/
├── enriched_leads.json     
├── summary.csv             
└── insights/
    ├── 01_greystar_TierA.txt
    ├── 02_goldoller_TierA.txt
    └── 04_davis_property_TierC.txt
```

---

# Thank You