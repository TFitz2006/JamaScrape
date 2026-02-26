from pathlib import Path

ROOT = Path("jama_out")   # change if needed
TARGET = "article.md"     # the md file name you currently have

def main() -> None:
    if not ROOT.exists() or not ROOT.is_dir():
        raise SystemExit(f"Root folder not found or not a directory: {ROOT}")

    renamed = 0
    skipped = 0

    for md_path in ROOT.rglob(TARGET):
        if not md_path.is_file():
            continue

        parent = md_path.parent
        new_name = f"{parent.name}.md"
        new_path = parent / new_name

        # If it's already correct, skip
        if md_path.name == new_name:
            skipped += 1
            continue

        # Avoid overwriting something that already exists
        if new_path.exists():
            print(f"SKIP (already exists): {new_path}")
            skipped += 1
            continue

        md_path.rename(new_path)
        print(f"RENAMED: {md_path} -> {new_path}")
        renamed += 1

    print(f"\nDone. Renamed: {renamed}, Skipped: {skipped}")

if __name__ == "__main__":
    main()