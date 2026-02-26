import json
import re
import sys
from typing import Any, Dict, List

from bs4 import BeautifulSoup, Tag

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# -----------------------------
# Fetch + search helpers
# -----------------------------

def load_titles(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Expected: list of {"title": "...", "url": "...", "page": ...}
    return data


def find_by_title(records: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    q = query.strip().lower()
    return [r for r in records if q in (r.get("title", "").lower())]


def journal_from_url(url: str) -> str:
    m = re.search(r"/journals/([^/]+)/", url)
    return m.group(1) if m else ""


def fetch_html(url: str, timeout: int = 30, headless: bool = True, debug_save: bool = False) -> str:
    """
    Fetch the fully-rendered HTML using Selenium.

    If your institutional access requires logging in, run with headless=False once,
    complete the login in the browser window, and retry.
    """
    options = webdriver.ChromeOptions()

    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1400,900")

    # Normal-looking user agent helps reduce bot walls
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0 Safari/537.36"
    )

    driver = webdriver.Chrome(options=options)
    try:
        driver.get(url)

        # Wait until the full-text container is present
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.article-body div.article-full-text"))
        )

        html = driver.page_source

        if debug_save:
            with open("debug_downloaded.html", "w", encoding="utf-8") as f:
                f.write(html)

        # Sanity checks: if these are missing, you probably got a shell/blocked page
        if "article-body" not in html or "article-full-text" not in html:
            raise RuntimeError("Downloaded HTML missing expected article content. Likely blocked/SSO/consent gate.")

        return html
    finally:
        driver.quit()


# -----------------------------
# Parser
# -----------------------------

def parse_clinical_challenge_fulltext(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    aft = soup.select_one(
    "div.article-body div.widget-instance-AMA_ArticleFulltext_New div.article-full-text"
)
    if not aft:
    # broader fallbacks (safe)
        aft = soup.select_one("div.article-body div.article-full-text[data-userhasaccess]")
    if not aft:
     aft = soup.select_one("div.article-body div.article-full-text")
    if not aft:
        return {"error": "fulltext container not found"}

    title_el = soup.select_one("h1.meta-article-title")
    title = title_el.get_text(" ", strip=True) if title_el else ""

    case_parts: List[str] = []
    discussion_parts: List[str] = []
    diagnosis_text = ""
    quiz_prompt = ""
    choices: List[str] = []
    figures: List[Dict[str, str]] = []

    in_case = False
    in_discussion_region = False
    got_diagnosis = False
    expect_diagnosis_para = False

    def capture_figure(fig: Tag):
        label = fig.select_one(".figure-label")
        label_text = label.get_text(" ", strip=True) if label else ""
        caption = fig.select_one(".figure-caption p.para")
        caption_text = caption.get_text(" ", strip=True) if caption else ""

        img = fig.select_one("img.content-img")
        thumb = (img.get("src") or img.get("data-original") or "") if img else ""

        view_large = fig.select_one("a.view-large[href]")
        full = view_large.get("href") if view_large else ""

        figures.append(
            {
                "label": label_text,
                "caption": caption_text,
                "thumb_url": thumb,
                "full_url": full,
            }
        )

    def get_heading_text(div: Tag) -> str:
        ht = div.select_one(".heading-text")
        return ht.get_text(" ", strip=True) if ht else ""

    for node in aft.descendants:
        if not isinstance(node, Tag):
            continue

        # Stop when hitting end matter
        if node.name == "div" and ("h3" in (node.get("class") or []) or "h4" in (node.get("class") or [])):
            heading = get_heading_text(node)
            if heading in ("Article Information", "References"):
                break

        # H3 sections
        if node.name == "div" and "h3" in (node.get("class") or []):
            heading = get_heading_text(node)
            if heading == "Case":
                in_case = True
                in_discussion_region = False
                continue
            if heading == "Discussion":
                in_case = False
                in_discussion_region = True
                continue

        # Diagnosis header inside discussion region
        if in_discussion_region and node.name == "div" and "h4" in (node.get("class") or []):
            heading = get_heading_text(node)
            if heading == "Diagnosis":
                expect_diagnosis_para = True
                continue

        # Quiz box
        if node.name == "div":
            classes = node.get("class") or []
            if "box-section" in classes and "online-quiz" in classes:
                h = node.select_one("h4.box-section--title")
                quiz_prompt = h.get_text(" ", strip=True) if h else quiz_prompt
                for li in node.select("ol.alpha-upper > li"):
                    t = li.get_text(" ", strip=True)
                    if t:
                        choices.append(t)
                in_case = False
                continue

        # Figures
        if node.name == "div" and "figure-table-wrapper" in (node.get("class") or []):
            capture_figure(node)

        # Paragraphs
        if node.name == "p" and "para" in (node.get("class") or []):
            txt = node.get_text(" ", strip=True)
            if not txt:
                continue

            if expect_diagnosis_para and not got_diagnosis:
                diagnosis_text = txt
                got_diagnosis = True
                expect_diagnosis_para = False
                continue

            if in_case:
                case_parts.append(txt)
            elif in_discussion_region and got_diagnosis:
                discussion_parts.append(txt)

        # Include H4 subheadings in discussion after diagnosis
        if in_discussion_region and got_diagnosis and node.name == "div" and "h4" in (node.get("class") or []):
            heading = get_heading_text(node)
            if heading and heading != "Diagnosis":
                discussion_parts.append(f"### {heading}")

    return {
        "title": title,
        "case_text": "\n\n".join(case_parts).strip(),
        "quiz_prompt": quiz_prompt,
        "choices": choices,
        "diagnosis": diagnosis_text,
        "discussion_text": "\n\n".join(discussion_parts).strip(),
        "figures": figures,
    }


# -----------------------------
# Renderer
# -----------------------------

def render_markdown(url: str, parsed: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"# {parsed.get('title','').strip()}")
    lines.append(f"URL: {url}")
    lines.append(f"Journal: {journal_from_url(url)}")
    lines.append("")

    lines.append("## Case")
    lines.append(parsed.get("case_text", "") or "(not found)")
    lines.append("")

    lines.append("## Figures")
    figs = parsed.get("figures") or []
    if not figs:
        lines.append("(none found)")
    else:
        for i, f in enumerate(figs, 1):
            label = f.get("label") or f"Figure {i}."
            caption = f.get("caption") or ""
            lines.append(f"- **{label.strip()}** {caption}".strip())
            if f.get("thumb_url"):
                lines.append(f"  - thumb: {f['thumb_url']}")
            if f.get("full_url"):
                lines.append(f"  - full: {f['full_url']}")
    lines.append("")

    lines.append("## Question")
    qp = parsed.get("quiz_prompt") or "What is your diagnosis?"
    lines.append(qp)
    ch = parsed.get("choices") or []
    if not ch:
        lines.append("(no choices found)")
    else:
        for idx, c in enumerate(ch):
            letter = chr(ord("A") + idx)
            lines.append(f"{letter}. {c}")
    lines.append("")

    lines.append("## Diagnosis")
    lines.append(parsed.get("diagnosis") or "(not found)")
    lines.append("")

    lines.append("## Discussion")
    lines.append(parsed.get("discussion_text") or "(not found)")
    return "\n".join(lines)


# -----------------------------
# CLI
# -----------------------------

def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  python jama_tool_min.py <jama_titles.json> <title substring>")
        print("")
        print("Tip: if access requires login, run once with HEADLESS=0:")
        print("  HEADLESS=0 python jama_tool_min.py jama_titles.json \"Retroperitoneal\"")
        sys.exit(1)

    titles_path = sys.argv[1]
    query = " ".join(sys.argv[2:])

    records = load_titles(titles_path)
    matches = find_by_title(records, query)

    if not matches:
        print(f'No matches for "{query}".')
        sys.exit(2)

    chosen = matches[0]
    url = chosen["url"]

    # Headless can be disabled via env var-like CLI pattern:
    # simplest: check an optional env var string in sys.argv
    # We'll keep it simple: if user sets HEADLESS=0 in environment, they can flip it.
    headless = True
    for arg in sys.argv:
        if arg.strip().upper() == "HEADLESS=0":
            headless = False

    html = fetch_html(url, headless=headless, debug_save=True)
    parsed = parse_clinical_challenge_fulltext(html)

    if parsed.get("error"):
        print("Parse error:", parsed["error"])
        sys.exit(3)

    md = render_markdown(url, parsed)
    print(md)


if __name__ == "__main__":
    main()