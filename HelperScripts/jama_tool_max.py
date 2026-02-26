import json
import re
import sys
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup, Tag
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# -----------------------------
# Helpers: load/search
# -----------------------------

def load_titles(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_by_title(records: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    q = query.strip().lower()
    return [r for r in records if q in (r.get("title", "").lower())]


def journal_from_url(url: str) -> str:
    m = re.search(r"/journals/([^/]+)/", url)
    return m.group(1) if m else ""


def parse_kv_args(argv: List[str]) -> Dict[str, str]:
    """
    Parses CLI args like HEADLESS=0 OUTPUT=foo.md
    """
    out: Dict[str, str] = {}
    for a in argv:
        if "=" in a:
            k, v = a.split("=", 1)
            out[k.strip().upper()] = v.strip()
    return out


# -----------------------------
# Fetch HTML via Selenium
# -----------------------------

def fetch_html(url: str, timeout: int = 30, headless: bool = True, debug_save: bool = False) -> str:
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1400,900")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0 Safari/537.36"
    )

    driver = webdriver.Chrome(options=options)
    try:
        driver.get(url)

        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.article-body div.article-full-text"))
        )

        html = driver.page_source

        if debug_save:
            with open("debug_downloaded.html", "w", encoding="utf-8") as f:
                f.write(html)

        if "article-body" not in html:
            raise RuntimeError("Downloaded HTML missing article-body. Likely blocked/SSO/consent gate.")

        return html
    finally:
        driver.quit()


# -----------------------------
# Text extraction: plain text (no tags)
# -----------------------------

def text_plain(tag: Tag) -> str:
    """
    Plain text only (no HTML tags), but fixes spacing artifacts caused by inline nodes.
    """
    s = tag.get_text(" ", strip=True)
    s = re.sub(r"\s+", " ", s).strip()

    # Remove space before punctuation
    s = re.sub(r"\s+([,.;:)\]\}])", r"\1", s)

    # Remove space after opening punctuation
    s = re.sub(r"([(\[\{])\s+", r"\1", s)

    # Fix isotopes: join digit + space + element symbol (uppercase + optional lowercase)
    # (won't touch "2 months" because months starts lowercase)
    s = re.sub(r"(\d)\s+([A-Z][a-z]?)\b", r"\1\2", s)

    # Fix common biomedical plus spacing like CD138 + -> CD138+
    s = re.sub(r"(CD\d+)\s*\+\s*", r"\1+", s)

    # IMPORTANT: ensure CD138+ is separated from the next word
    # CD138+plasma -> CD138+ plasma
    s = re.sub(r"(CD\d+\+)(?=[A-Za-z])", r"\1 ", s)

    # Fix spaced minus/exponent like 10 −5 -> 10−5 (also works for 10 -5)
    s = re.sub(r"(\d)\s*([−-])\s*(\d)", r"\1\2\3", s)

    # Fix spaced comma patterns like "2 , 5" -> "2, 5"
    s = re.sub(r"\s*,\s*", r", ", s)
    s = re.sub(r",\s+", r", ", s)

    # IMPORTANT: add missing space after reference number at sentence boundary
    # ". 7In" -> ". 7 In"   (won't affect 18F because it's not after ". ")
    s = re.sub(r"(\.\s*\d{1,2})(?=[A-Z])", r"\1 ", s)

    return s


# -----------------------------
# Parser helpers
# -----------------------------

def pick_best_fulltext_container(soup: BeautifulSoup) -> Optional[Tag]:
    """
    JAMA pages often have multiple div.article-full-text blocks.
    We want the one that actually contains the Case/Discussion sections.
    """
    candidates = soup.select("div.article-body div.article-full-text")
    if not candidates:
        return None

    # Prefer container that contains a Case header.
    for c in candidates:
        if c.select_one("div.h3 .heading-text") and "Case" in c.get_text(" ", strip=True):
            return c

    # Next: any container that contains an online quiz box (clinical challenges)
    for c in candidates:
        if c.select_one("div.box-section.online-quiz"):
            return c

    # Next: container that has any h3 headings
    for c in candidates:
        if c.select_one("div.h3"):
            return c

    # Fallback: last candidate (often the real one)
    return candidates[-1]


# -----------------------------
# Main parser
# -----------------------------

def parse_clinical_challenge_fulltext(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    aft = pick_best_fulltext_container(soup)
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

    def get_heading_text(div: Tag) -> str:
        ht = div.select_one(".heading-text")
        return ht.get_text(" ", strip=True) if ht else ""

    def capture_figure(fig: Tag) -> None:
        label = fig.select_one(".figure-label")
        label_text = label.get_text(" ", strip=True) if label else ""

        caption_p = fig.select_one(".figure-caption p.para")
        caption_text = text_plain(caption_p) if caption_p else ""

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

    for node in aft.descendants:
        if not isinstance(node, Tag):
            continue

        # stop at end matter
        if node.name == "div" and any(cls in (node.get("class") or []) for cls in ("h3", "h4")):
            heading = get_heading_text(node)
            if heading in ("Article Information", "References"):
                break

        # section start (h3)
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

        # h4 headings inside discussion (Diagnosis + subheadings)
        if in_discussion_region and node.name == "div" and "h4" in (node.get("class") or []):
            heading = get_heading_text(node)

            if heading == "Diagnosis":
                expect_diagnosis_para = True
                continue

            # after diagnosis, include subheadings but avoid redundant "Discussion"
            if got_diagnosis and heading and heading not in ("Discussion",):
                discussion_parts.append(f"### {heading}")
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

                # quiz ends the "case narrative" region
                in_case = False
                continue

        # Figures
        if node.name == "div" and "figure-table-wrapper" in (node.get("class") or []):
            capture_figure(node)
            continue

        # Paragraphs
        if node.name == "p" and "para" in (node.get("class") or []):
            # Avoid redundancy: don't capture figure captions into case/discussion
            if node.find_parent("div", class_="figure-table-wrapper") is not None:
                continue

            # Skip quiz paragraphs
            if node.find_parent("div", class_="box-section") is not None:
                continue

            txt = text_plain(node)
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
# Renderer (markdown)
# -----------------------------

def render_markdown(url: str, parsed: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"# {parsed.get('title', '').strip()}")
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
            label = (f.get("label") or f"Figure {i}.").strip()
            caption = (f.get("caption") or "").strip()
            bullet = f"- **{label}**"
            if caption:
                bullet += f" {caption}"
            lines.append(bullet)

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

def main() -> None:
    if len(sys.argv) < 3:
        print("Usage:")
        print("  python jama_tool_max.py <jama_titles.json> <title substring> [HEADLESS=0] [OUTPUT=article_output.md]")
        sys.exit(1)

    titles_path = sys.argv[1]
    query = " ".join([a for a in sys.argv[2:] if "=" not in a])

    kv = parse_kv_args(sys.argv[2:])
    headless = kv.get("HEADLESS", "1") != "0"
    out_path = kv.get("OUTPUT", "article_output.md")

    records = load_titles(titles_path)
    matches = find_by_title(records, query)

    if not matches:
        print(f'No matches for "{query}".')
        sys.exit(2)

    url = matches[0]["url"]

    html = fetch_html(url, headless=headless, debug_save=True)
    parsed = parse_clinical_challenge_fulltext(html)

    if parsed.get("error"):
        print("Parse error:", parsed["error"])
        sys.exit(3)

    md = render_markdown(url, parsed)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)
        f.write("\n")

    print(f"Saved markdown to: {out_path}")


if __name__ == "__main__":
    main()