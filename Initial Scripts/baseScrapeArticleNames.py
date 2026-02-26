import json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

START = "https://jamanetwork.com/collections/44038/clinical-challenge?fl_Categories=Clinical+Challenge&fl_ContentType=Article&page=1"

driver = webdriver.Chrome()
wait = WebDriverWait(driver, 8)

def maybe_accept_cookies():
    try:
        btn = WebDriverWait(driver, 2).until(
            EC.element_to_be_clickable((By.XPATH, "//button[normalize-space()='Continue']"))
        )
        btn.click()
    except Exception:
        pass

page = 1
seen = set()
articles = []  # <-- collect here

while True:
    url = START.replace("page=1", f"page={page}")
    driver.get(url)
    maybe_accept_cookies()

    links = wait.until(
        EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a.article--title"))
    )

    new_rows = 0
    for a in links:
        title = a.text.strip()
        href = (a.get_attribute("href") or "").strip()

        if not title or not href:
            continue

        key = (title, href)
        if key in seen:
            continue

        seen.add(key)
        new_rows += 1
        articles.append({
            "title": title,
            "url": href,
            "page": page
        })

    if new_rows == 0:
        break

    next_candidates = driver.find_elements(
        By.XPATH,
        "//a[contains(normalize-space(.), 'Next') and contains(@href, 'page=')]"
    )
    if not next_candidates:
        break

    page += 1

driver.quit()

# write to JSON file
with open("jama_titles.json", "w", encoding="utf-8") as f:
    json.dump(articles, f, ensure_ascii=False, indent=2)

print(f"Wrote {len(articles)} articles to jama_titles.json")