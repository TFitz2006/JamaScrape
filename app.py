import os
import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import streamlit as st

# -----------------------------
# Config
# -----------------------------
DEFAULT_ROOT = "jama_out"
INDEX_NAME = "index.json"

# markdown image: ![alt](path)
MD_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)")


# -----------------------------
# Utilities
# -----------------------------
def format_question_choices(md_text: str) -> str:
    """
    Make diagnosis answer choices display one-per-line.

    Supports:
      - Question line has inline choices: "What Is Your Diagnosis? A. ... B. ... C. ... D. ..."
      - Question line alone, choices on following lines:
            What Is Your Diagnosis?
            A. ...
            B. ...
            C. ...
            D. ...
      - Mixed: A on next line, B/C/D follow on subsequent lines
    """
    lines = md_text.splitlines()
    out: List[str] = []

    q_re = re.compile(r"^\s*What\s+Is\s+Your\s+Diagnosis\?\s*(.*)$", re.IGNORECASE)
    # identifies a choice line like "B. something..."
    choice_line_re = re.compile(r"^\s*([A-D])\.\s*(.+?)\s*$")
    # splits inline choices inside a single string
    inline_split_re = re.compile(r"\s*([A-D])\.\s*")

    i = 0
    while i < len(lines):
        line = lines[i]
        m = q_re.match(line)

        if not m:
            out.append(line)
            i += 1
            continue

        rest = (m.group(1) or "").strip()
        choices: List[Tuple[str, str]] = []

        # 1) If choices are inline after the question mark
        if rest and re.search(r"\bA\.\s*", rest):
            parts = inline_split_re.split(rest)
            # parts: ["", "A", "textA", "B", "textB", ...]
            j = 1
            while j + 1 < len(parts):
                letter = parts[j]
                text = parts[j + 1].strip()
                if text:
                    choices.append((letter, text))
                j += 2

        # 2) If not inline (or incomplete), pull from subsequent lines that look like "A. ...", "B. ...", etc.
        #    Also handles the case where the question line has no rest.
        k = i + 1
        while k < len(lines):
            m2 = choice_line_re.match(lines[k])
            if not m2:
                break
            letter = m2.group(1)
            text = m2.group(2).strip()
            if text:
                # avoid duplicating if we already captured it inline
                if not any(letter == L for (L, _) in choices):
                    choices.append((letter, text))
            k += 1

        # If we found choices, render them as a markdown list (guaranteed one-per-line)
        if choices:
            # consume any choice lines we captured from following lines
            i = k

            out.append("What Is Your Diagnosis?")
            out.append("")  # blank line before list helps markdown rendering
            for letter, text in choices:
                out.append(f"- {letter}. {text}")
            out.append("")  # blank line after list
            continue

        # If we didn't find choices, just keep the original question line
        out.append(line)
        i += 1

    return "\n".join(out)

def safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        try:
            return path.read_text(errors="ignore")
        except Exception:
            return ""


def load_index(root: Path) -> List[Dict[str, Any]]:
    index_path = root / INDEX_NAME
    if not index_path.exists():
        return []
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def find_markdown_path_from_record(root: Path, rec: Dict[str, Any]) -> Optional[Path]:
    """
    index.json usually contains 'markdown' with an absolute path.
    If not, we try to locate a .md file inside the folder.
    """
    md = rec.get("markdown")
    if md:
        p = Path(md)
        if p.is_absolute():
            return p if p.exists() else None

        # relative to cwd
        p2 = Path(md)
        if p2.exists():
            return p2

        # relative to root
        p3 = root / md
        if p3.exists():
            return p3

    folder = rec.get("folder")
    if folder:
        fp = Path(folder)
        if not fp.is_absolute():
            fp = Path.cwd() / fp
        if fp.exists() and fp.is_dir():
            mds = list(fp.glob("*.md"))
            return mds[0] if mds else None

    return None


def extract_title_from_md(md_text: str) -> str:
    m = re.search(r"^\s*#\s+(.+?)\s*$", md_text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def build_search_blob(md_text: str) -> str:
    return re.sub(r"\s+", " ", md_text).strip().lower()


def score_record(query: str, title: str, blob: str) -> float:
    q = query.strip().lower()
    if not q:
        return 0.0

    score = 0.0
    t_lower = (title or "").lower()

    if q in t_lower:
        score += 50.0

    tokens = [t for t in re.split(r"\s+", q) if t]
    for tok in tokens:
        if tok in t_lower:
            score += 10.0
        if tok in blob:
            score += 1.0

    return score


def list_image_files(md_path: Path) -> List[Path]:
    img_dir = md_path.parent / "images"
    if not img_dir.exists() or not img_dir.is_dir():
        return []
    imgs: List[Path] = []
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif"):
        imgs.extend(img_dir.glob(ext))
    return sorted(imgs, key=lambda p: p.name)


def resolve_md_image_src(md_path: Path, src: str) -> Optional[Path]:
    """
    Resolve markdown image src to a local file if possible.
    - supports src like "images/figure_1.png"
    - supports src like "./images/figure_1.png"
    """
    s = (src or "").strip().strip('"').strip("'")
    if not s:
        return None

    # if it's a URL, let markdown handle it (we won't st.image it)
    if s.startswith("http://") or s.startswith("https://"):
        return None

    # normalize ./ prefix
    if s.startswith("./"):
        s = s[2:]

    # relative to the markdown file folder
    p = (md_path.parent / s).resolve()
    return p if p.exists() and p.is_file() else None


def render_markdown_with_inline_images(md_text: str, md_path: Path, show_images: bool) -> None:
    """
    Renders markdown but intercepts local markdown image tags and
    displays them inline via st.image() (so they appear in the correct spot).
    """
    if not md_text.strip():
        st.info("Empty markdown.")
        return

    buffer_lines: List[str] = []

    def flush():
        if buffer_lines:
            st.markdown("\n".join(buffer_lines), unsafe_allow_html=False)
            buffer_lines.clear()

    for line in md_text.splitlines():
        m = MD_IMAGE_RE.search(line)
        if not m:
            buffer_lines.append(line)
            continue

        # if the line has an image tag, flush text up to here
        flush()

        alt = (m.group("alt") or "").strip()
        src = (m.group("src") or "").strip()
        local_img = resolve_md_image_src(md_path, src)

        # If we can render it locally and user wants images, do it
        if show_images and local_img:
            st.image(str(local_img), caption=alt or local_img.name, use_container_width=True)
        else:
            # fallback: render the original markdown line (works for remote URLs)
            st.markdown(line, unsafe_allow_html=False)

    flush()


# -----------------------------
# Cached loaders
# -----------------------------
@st.cache_data(show_spinner=False)
def load_library(root_str: str) -> List[Dict[str, Any]]:
    root = Path(root_str)
    idx = load_index(root)

    records: List[Dict[str, Any]] = []
    for rec in idx:
        if not rec.get("ok"):
            continue

        md_path = find_markdown_path_from_record(root, rec)
        if not md_path or not md_path.exists():
            continue

        md_text = safe_read_text(md_path)
        title = rec.get("title") or extract_title_from_md(md_text) or "Untitled"
        journal = rec.get("journal") or ""
        url = rec.get("url") or ""

        records.append(
            {
                "title": title,
                "journal": journal,
                "url": url,
                "md_path": str(md_path),
                "md_text": md_text,
                "blob": build_search_blob(md_text),
            }
        )

    return records


# -----------------------------
# UI
# -----------------------------
st.set_page_config(page_title="JAMA Article Library", layout="wide")
st.title("JAMA Article Library")

# Minimal sidebar: only root folder
with st.sidebar:
    st.header("Library")
    root = st.text_input("Root folder", value=DEFAULT_ROOT)
    st.caption("Folder containing index.json and article folders.")
    st.divider()
    st.caption("If you move jama_out, update the path here.")

records = load_library(root)

if not records:
    st.error(
        f"Could not load library from '{root}'. Make sure '{root}/{INDEX_NAME}' exists and contains ok=true records."
    )
    st.stop()

# Top control bar (easy to find)
journals = sorted({(r["journal"] or "").strip() for r in records if (r["journal"] or "").strip()})
journal_options = ["All"] + journals

c1, c2, c3, c4 = st.columns([0.55, 0.20, 0.12, 0.13], gap="medium")
with c1:
    query = st.text_input("Search", value="", placeholder="Search title or full text…")
with c2:
    journal_choice = st.selectbox("Journal", options=journal_options, index=0)
with c3:
    only_successful = st.toggle("Only successful", value=True)
with c4:
    show_images = st.toggle("Show images", value=True)

# Filter base
filtered = records[:]
if only_successful:
    # records already are ok-only from index, so this is mostly future-proof
    filtered = filtered

if journal_choice != "All":
    jf = journal_choice.lower()
    filtered = [r for r in filtered if jf == (r["journal"] or "").lower()]

# Search + rank
q = query.strip()
if q:
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for r in filtered:
        s = score_record(q, r["title"], r["blob"])
        if s > 0:
            scored.append((s, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    filtered = [r for _, r in scored]

st.caption(f"Results: {len(filtered)}")

if not filtered:
    st.info("No matches. Try fewer keywords or switch Journal back to All.")
    st.stop()

# Article picker (no left list)
selected = st.selectbox(
    "Select an article",
    options=filtered,
    format_func=lambda r: f"{r['title']} — {r['journal']}",
)

st.divider()

# Article view
st.subheader(selected["title"])
meta_cols = st.columns([0.25, 0.75], gap="small")
with meta_cols[0]:
    st.write(f"**Journal:** {selected['journal']}")
with meta_cols[1]:
    if selected["url"]:
        st.write(f"**URL:** {selected['url']}")

md_path = Path(selected["md_path"])
st.caption(f"File: {md_path}")

# Render markdown with inline images in the correct spot
md_text = format_question_choices(selected["md_text"])
render_markdown_with_inline_images(md_text, md_path, show_images)

# Optional: if you still want a gallery at the bottom, uncomment this block.
# if show_images:
#     imgs = list_image_files(md_path)
#     if imgs:
#         st.divider()
#         st.subheader("All images")
#         for img in imgs:
#             st.image(str(img), caption=img.name, use_container_width=True)