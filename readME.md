# JAMA Article Library (Local Search + Viewer)

This repo contains a small toolchain to do two things:

1. Convert JAMA Network articles into a local library of Markdown files and images
2. Let research team search and read that local library in a simple web UI

A ZIP of the generated article folders (the `jama_out/` library) will be included in the repo, or provided alongside it.

---

## What’s in this repo

- `app.py`  
  Streamlit web app for searching and viewing the local Markdown library and images.

- `jama_batch.py` (or your batch scraper script name)  
  Scrapes articles from an input JSON and generates:
  - `jama_out/<article_folder>/article.md`
  - `jama_out/<article_folder>/images/*`
  - `jama_out/index.json`

- `jama_titles.json`  
  Input list of articles with at least `title` and `url`.

- `jama_out.zip` (or provided separately)  
  The scraped library output. When unzipped, it should create a folder like:
  - `jama_out/index.json`
  - `jama_out/<many article folders>/article.md`
  - `jama_out/<many article folders>/images/*`

---

## Requirements

- Python 3.9+ (3.10+ recommended)
- pip
- For scraping (optional):
  - Google Chrome
  - ChromeDriver compatible with your Chrome version

Works on macOS, Windows, and Linux.

---

## Quick start (view/search the existing articles)

These steps assume you already have the scraped library (`jama_out.zip` or `jama_out/`).

### 1. Put the article library in the right place

If the repo already contains `jama_out/`, skip this.

If the repo contains `jama_out.zip`:

- Unzip `jama_out.zip` in the project root (the same folder as `app.py`)
- After unzipping, you must have:

  - `jama_out/index.json`
  - `jama_out/<many folders>/article.md`
  - `jama_out/<many folders>/images/*`

Expected layout:

project_root/
  app.py
  requirements.txt
  jama_out/
    index.json
    Some_Article_Title__abcdef123456/
      article.md
      images/
        figure_1.png
        figure_2.png

### 2. Create a virtual environment

macOS / Linux:

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip

Windows (PowerShell):

py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip

### 3. Install dependencies

pip install -r requirements.txt

If you don’t have a requirements file yet, the minimum to run the viewer app is:

pip install streamlit

### 4. Run the app

streamlit run app.py

Streamlit will print a local URL, typically:

http://localhost:8501

### 5. Use the app

- Root folder should be `jama_out` (default)
- Search with a few keywords (title and full text are indexed)
- Use the Journal dropdown to filter
- Toggle images on/off
- Images should appear inline in the correct spot when the Markdown contains `![...](images/...)`

---

## Troubleshooting (Viewer App)

### “Could not load library…”
This means the app cannot find `jama_out/index.json` or cannot find the markdown paths referenced by the index.

Fix:
- Confirm you have `jama_out/index.json`
- Confirm `jama_out/` is in the project root (same folder as `app.py`)
- If `jama_out` is elsewhere, update the “Root folder” field in the app to that path

### Images not showing
The app expects images in:

<article_folder>/images/

Fix:
- Confirm each article folder has an `images/` directory with figure files
- Confirm the Markdown file sits in the same folder as `images/` (sibling directory)
- If Markdown has inline image tags like `![Figure](images/figure_1.png)`, those should render in place

---

## Scraping / Re-scraping (Optional)

Only do this if you want to regenerate `jama_out/`.

### Scraping prerequisites
- Chrome installed
- ChromeDriver installed and compatible with your Chrome version

### Input file
`jama_titles.json` should be a JSON list like:

[
  {"title": "Some article", "url": "https://jamanetwork.com/..."},
  ...
]

### Run the batch scraper

Example:

python3 jama_batch.py jama_titles.json OUTDIR=jama_out DOWNLOAD_IMAGES=1 RESUME=1

Common options:
- OUTDIR=jama_out  
  Where to write output (use the same folder name the app expects)
- DOWNLOAD_IMAGES=1  
  Downloads images into each article folder under `images/`
- HEADLESS=0  
  Opens a real browser window (useful for login/SSO)
- RESUME=1  
  Uses existing `jama_out/index.json` and skips URLs already marked ok=true
- LIMIT=50  
  Only scrape the first N titles
- RETRIES=3  
  Retry failed pages a few times

Example non-headless run:

python3 jama_batch.py jama_titles.json HEADLESS=0 OUTDIR=jama_out DOWNLOAD_IMAGES=1 RESUME=1

### Output
The scraper writes:
- `jama_out/index.json` (run log + metadata)
- A folder per article:
  - `article.md`
  - `images/figure_*.png` (when DOWNLOAD_IMAGES=1)

---

## GitHub sharing and what to commit

### Recommended files to commit
- app.py
- jama_batch.py (and any helper scripts)
- jama_titles.json
- README.md
- requirements.txt
- jama_out.zip (or jama_out/ if it is not too large)

### Do not commit
- .venv/
- __pycache__/
- *.pyc
- .DS_Store
- debug_downloaded.html

Suggested `.gitignore`:

.venv/
__pycache__/
*.pyc
.DS_Store
debug_downloaded.html

---

##  Quick Checklist

1. Ensure `jama_out/` exists and contains `index.json`
   - If provided as `jama_out.zip`, unzip it into the project root

2. Set up environment
   - python3 -m venv .venv
   - source .venv/bin/activate
   - pip install -r requirements.txt

3. Run the viewer
   - streamlit run app.py

4. Confirm:
   - Search returns results
   - Selecting an article displays the markdown
   - Images render inline

---

## Notes
- The viewer app works offline as long as `jama_out/` exists locally.
- Scraping requires live access to JAMA Network and may require institutional login.