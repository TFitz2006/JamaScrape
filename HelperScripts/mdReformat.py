from pathlib import Path

ROOT = Path("jama_out")   # change if needed

def main() -> None:
    changed = 0
    for md_path in ROOT.rglob("*.md"):
        text = md_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        new_lines = []
        removed_any = False
        for line in lines:
            if line.lstrip().startswith("- full:") or line.lstrip().startswith("- thumb:"):
                removed_any = True
                continue
            new_lines.append(line)

        if removed_any:
            md_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            changed += 1

    print(f"Done. Updated {changed} markdown files.")

if __name__ == "__main__":
    main()