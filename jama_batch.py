import json
import os
import re
import sys
import time
import hashlib
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from urllib.request import urlretrieve

from bs4 import BeautifulSoup, Tag

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# -----------------------------
# Helpers: load/search + CLI kv
# -----------------------------

def load_titles(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_existing_index(outdir: str) -> List[Dict[str, Any]]:
    path = os.path.join(outdir, "index.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def build_ok_url_set(existing: List[Dict[str, Any]]) -> set:
    ok = set()
    for r in existing:
        if r.get("ok") and r.get("url"):
            ok.add(r["url"])
    return ok


def write_index(outdir: str, results: List[Dict[str, Any]]) -> str:
    index_path = os.path.join(outdir, "index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    return index_path


def parse_kv_args(argv: List[str]) -> Dict[str, str]:
    """
    Parses CLI args like HEADLESS=0 OUTDIR=out LIMIT=25 DOWNLOAD_IMAGES=1 INLINE_IMAGES=1
    """
    out: Dict[str, str] = {}
    for a in argv:
        if "=" in a:
            k, v = a.split("=", 1)
            out[k.strip().upper()] = v.strip()
    return out


def journal_from_url(url: str) -> str:
    m = re.search(r"/journals/([^/]+)/", url)
    return m.group(1) if m else ""


def safe_filename(s: str, max_len: int = 120) -> str:
    s = (s or "").strip()
    s = re.sub(r"[^\w\s.-]", "", s)          # remove odd chars
    s = re.sub(r"\s+", "_", s)              # spaces -> _
    s = s.strip("._-")
    if not s:
        s = "untitled"
    return s[:max_len]


def article_id(url: str) -> str:
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return h


# -----------------------------
# Fetch HTML via Selenium
# -----------------------------

def make_driver(headless: bool) -> webdriver.Chrome:
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
    return webdriver.Chrome(options=options)


def fetch_html(
    driver: webdriver.Chrome,
    url: str,
    timeout: int = 30,
    debug_save: bool = False
) -> str:
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


# -----------------------------
# Text extraction (NO tags)
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
    s = re.sub(r"(\d)\s+([A-Z][a-z]?)\b", r"\1\2", s)

    # Fix common biomedical plus spacing like CD138 + -> CD138+
    s = re.sub(r"(CD\d+)\s*\+\s*", r"\1+", s)

    # Ensure CD138+ is separated from the next word
    s = re.sub(r"(CD\d+\+)(?=[A-Za-z])", r"\1 ", s)

    # Fix spaced minus/exponent like 10 −5 -> 10−5 (also works for 10 -5)
    s = re.sub(r"(\d)\s*([−-])\s*(\d)", r"\1\2\3", s)

    # Fix spaced comma patterns like "2 , 5" -> "2, 5"
    s = re.sub(r"\s*,\s*", r", ", s)
    s = re.sub(r",\s+", r", ", s)

    # Add missing space after reference number at sentence boundary: ". 7In" -> ". 7 In"
    s = re.sub(r"(\.\s*\d{1,2})(?=[A-Z])", r"\1 ", s)

    return s


# -----------------------------
# Parser
# -----------------------------

def pick_best_fulltext_container(soup: BeautifulSoup) -> Optional[Tag]:
    candidates = soup.select("div.article-body div.article-full-text")
    if not candidates:
        return None

    # Prefer: contains h3 headings and the word "Case"
    for c in candidates:
        if c.select_one("div.h3 .heading-text") and "Case" in c.get_text(" ", strip=True):
            return c

    # Next: any container that contains quiz box
    for c in candidates:
        if c.select_one("div.box-section.online-quiz"):
            return c

    # Next: has any h3 headings
    for c in candidates:
        if c.select_one("div.h3"):
            return c

    return candidates[-1]


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
    in_discussion = False
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
            {"label": label_text, "caption": caption_text, "thumb_url": thumb, "full_url": full}
        )

    last_added_h4: Optional[str] = None

    for node in aft.descendants:
        if not isinstance(node, Tag):
            continue

        # stop at end matter
        if node.name == "div" and any(cls in (node.get("class") or []) for cls in ("h3", "h4")):
            heading0 = get_heading_text(node)
            if heading0 in ("Article Information", "References"):
                break

        # section starts (h3)
        if node.name == "div" and "h3" in (node.get("class") or []):
            h3 = get_heading_text(node)
            if h3 == "Case":
                in_case = True
                in_discussion = False
                continue
            if h3 == "Discussion":
                in_case = False
                in_discussion = True
                continue

        # diagnosis header (h4) inside discussion
        if in_discussion and node.name == "div" and "h4" in (node.get("class") or []):
            h4 = get_heading_text(node)

            if h4 == "Diagnosis":
                expect_diagnosis_para = True
                continue

            if got_diagnosis and h4 and h4 not in ("Discussion", "Diagnosis") and h4 != last_added_h4:
                discussion_parts.append(f"### {h4}")
                last_added_h4 = h4
            continue

        # quiz box
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

        # figures
        if node.name == "div" and "figure-table-wrapper" in (node.get("class") or []):
            capture_figure(node)
            continue

        # paragraphs
        if node.name == "p" and "para" in (node.get("class") or []):
            if node.find_parent("div", class_="figure-table-wrapper") is not None:
                continue
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
            elif in_discussion and got_diagnosis:
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
# Images: download (and return local relative paths)
# -----------------------------

def download_figures(figures: List[Dict[str, str]], out_dir: str) -> List[Dict[str, str]]:
    """
    Downloads figures into out_dir as figure_1.png, figure_2.png, etc.
    Returns a list with local_path + local_rel (relative path from article.md folder).
    """
    os.makedirs(out_dir, exist_ok=True)
    downloaded: List[Dict[str, str]] = []

    for idx, f in enumerate(figures, 1):
        url = f.get("full_url") or f.get("thumb_url") or ""
        if not url:
            downloaded.append({"figure": str(idx), "ok": "0", "error": "no url"})
            continue

        path = urlparse(url).path
        ext = os.path.splitext(path)[1] or ".png"
        fname = f"figure_{idx}{ext}"
        local_path = os.path.join(out_dir, fname)

        try:
            urlretrieve(url, local_path)
            downloaded.append(
                {
                    "figure": str(idx),
                    "ok": "1",
                    "url": url,
                    "local_path": local_path,
                    # article.md sits one folder above images/
                    "local_rel": os.path.join("images", fname),
                }
            )
        except Exception as e:
            downloaded.append({"figure": str(idx), "ok": "0", "url": url, "error": str(e)})

    return downloaded


# -----------------------------
# Renderer (markdown) with optional inline images
# -----------------------------

def render_markdown(
    url: str,
    parsed: Dict[str, Any],
    inline_images: bool = False,
    downloaded_images: Optional[List[Dict[str, str]]] = None,
) -> str:
    # Map "1" -> "images/figure_1.png"
    local_map: Dict[str, str] = {}
    if downloaded_images:
        for d in downloaded_images:
            if d.get("ok") == "1" and d.get("figure") and d.get("local_rel"):
                local_map[d["figure"]] = d["local_rel"]

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
            idx_str = str(i)
            label = (f.get("label") or f"Figure {i}.").strip()
            caption = (f.get("caption") or "").strip()

            bullet = f"- **{label}**"
            if caption:
                bullet += f" {caption}"
            lines.append(bullet)

            # INLINE: prefer local downloaded image, otherwise fall back to full_url
            if inline_images:
                rel = local_map.get(idx_str)
                if rel:
                    lines.append(f"\n  ![{label}]({rel})\n")
                else:
                    img_url = f.get("full_url") or f.get("thumb_url") or ""
                    if img_url:
                        lines.append(f"\n  ![{label}]({img_url})\n")

            # If not inlining, or you still want links, keep concise links
            if not inline_images:
                if f.get("thumb_url"):
                    lines.append(f"  - thumb: {f['thumb_url']}")
                if f.get("full_url"):
                    lines.append(f"  - full: {f['full_url']}")
            else:
                # Keep a single "full" link (optional but handy)
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
# Batch runner
# -----------------------------
def run_one(
    driver: webdriver.Chrome,
    rec: Dict[str, Any],
    outdir: str,
    download_images: bool,
    timeout: int,
    sleep_s: float,
) -> Dict[str, Any]:
    url = rec.get("url", "")
    title_guess = rec.get("title", "") or ""
    jid = journal_from_url(url)
    aid = article_id(url)

    html = fetch_html(driver, url, timeout=timeout, debug_save=False)
    parsed = parse_clinical_challenge_fulltext(html)

    if parsed.get("error"):
        return {
            "url": url,
            "title": title_guess,
            "journal": jid,
            "id": aid,
            "ok": False,
            "error": parsed["error"],
        }

    title = parsed.get("title") or title_guess or aid
    slug = safe_filename(title)
    article_folder = os.path.join(outdir, f"{slug}__{aid}")
    os.makedirs(article_folder, exist_ok=True)

    # Download images first so we can inline local paths
    downloaded_images: List[Dict[str, str]] = []
    if download_images:
        imgs_dir = os.path.join(article_folder, "images")
        downloaded_images = download_figures(parsed.get("figures") or [], imgs_dir)

    # Write markdown
    md_path = os.path.join(article_folder, "article.md")
    md = render_markdown(
        url=url,
        parsed=parsed,
        inline_images=download_images,              # inline if we downloaded
        downloaded_images=downloaded_images,
    )
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
        f.write("\n")

    # Be polite to the site
    if sleep_s > 0:
        time.sleep(sleep_s)

    return {
        "url": url,
        "title": title,
        "journal": jid,
        "id": aid,
        "ok": True,
        "folder": article_folder,
        "markdown": md_path,
        "figures_count": len(parsed.get("figures") or []),
        "images": downloaded_images,
    }

def run_one_with_retries(
    headless: bool,
    rec: Dict[str, Any],
    outdir: str,
    download_images: bool,
    timeout: int,
    sleep_s: float,
    retries: int,
) -> Dict[str, Any]:
    last_err = None
    for attempt in range(1, retries + 1):
        driver = make_driver(headless=headless)
        try:
            return run_one(driver, rec, outdir, download_images, timeout, sleep_s)
        except Exception as e:
            last_err = e
            # small backoff before retrying
            time.sleep(min(2.0 * attempt, 6.0))
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    url = rec.get("url", "")
    title = rec.get("title", "") or ""
    return {
        "url": url,
        "title": title,
        "journal": journal_from_url(url),
        "id": article_id(url) if url else "",
        "ok": False,
        "error": f"Failed after {retries} attempts: {last_err}",
    }


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print(
            "  python3 jama_batch.py jama_titles.json "
            "[HEADLESS=0] [OUTDIR=out] [LIMIT=0] [DOWNLOAD_IMAGES=0] "
            "[TIMEOUT=30] [SLEEP=0.5] [RESUME=1] [RETRIES=3]"
        )
        sys.exit(1)

    titles_path = sys.argv[1]
    kv = parse_kv_args(sys.argv[2:])

    headless = kv.get("HEADLESS", "1") != "0"
    outdir = kv.get("OUTDIR", "jama_out")
    limit = int(kv.get("LIMIT", "0"))              # 0 => all
    download_images = kv.get("DOWNLOAD_IMAGES", "0") == "1"
    timeout = int(kv.get("TIMEOUT", "30"))
    sleep_s = float(kv.get("SLEEP", "0.5"))
    resume = kv.get("RESUME", "1") == "1"
    retries = int(kv.get("RETRIES", "3"))

    os.makedirs(outdir, exist_ok=True)
    records = load_titles(titles_path)
    if limit > 0:
        records = records[:limit]

    existing = load_existing_index(outdir) if resume else []
    ok_urls = build_ok_url_set(existing) if resume else set()

    # Start results with existing so index.json grows
    results: List[Dict[str, Any]] = existing[:] if resume else []

    # Track URLs already recorded in results to avoid duplicates
    recorded_urls = set(r.get("url") for r in results if r.get("url"))

    total = len(records)
    skipped = 0

    for i, rec in enumerate(records, 1):
        url = rec.get("url", "")
        title = rec.get("title", "") or ""

        if resume and url in ok_urls:
            skipped += 1
            continue

        print(f"[{i}/{total}] {title} :: {url}")

        r = run_one_with_retries(
            headless=headless,
            rec=rec,
            outdir=outdir,
            download_images=download_images,
            timeout=timeout,
            sleep_s=sleep_s,
            retries=retries,
        )

        # Replace existing record for same URL if present, otherwise append
        if url and url in recorded_urls:
            for idx in range(len(results) - 1, -1, -1):
                if results[idx].get("url") == url:
                    results[idx] = r
                    break
        else:
            results.append(r)
            if url:
                recorded_urls.add(url)

        # Save progress every article
        write_index(outdir, results)

    ok_count = sum(1 for r in results if r.get("ok"))
    print(f"\nDone. Success: {ok_count}/{len(results)}")
    print(f"Skipped already-done: {skipped}")
    print(f"Index saved to: {os.path.join(outdir, 'index.json')}")


if __name__ == "__main__":
    main()