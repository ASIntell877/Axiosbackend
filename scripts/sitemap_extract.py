import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import os
import json
import time
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser

# === CONFIGURATION ===
SITEMAP_URL = 'https://example.com/sitemap.xml'  # 🔁 Replace with your real sitemap URL
OUTPUT_DIR = './site_text'
os.makedirs(OUTPUT_DIR, exist_ok=True)

USER_AGENT = "MyRAGBot/1.0 (+https://yourdomain.com/info)"  # Update with your info
HEADERS = {
    "User-Agent": USER_AGENT
}

NAMESPACE = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

# === Parse robots.txt ===
def fetch_robots_txt(base_url):
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser()
    try:
        rp.set_url(robots_url)
        rp.read()
        print(f"Loaded robots.txt from {robots_url}")
    except Exception as e:
        print(f"Failed to load robots.txt: {e}")
    return rp

def can_fetch_url(rp, url):
    # Returns True if allowed by robots.txt, False otherwise
    return rp.can_fetch(USER_AGENT, url)

# === Sitemap parsing functions ===
def get_namespaced_tag(tag):
    return f"{{{NAMESPACE['ns']}}}{tag}"

def fetch_sitemap_urls(sitemap_url, rp):
    if not can_fetch_url(rp, sitemap_url):
        print(f"Blocked by robots.txt: {sitemap_url}")
        return []

    try:
        r = requests.get(sitemap_url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            print(f"Failed to fetch {sitemap_url}, status code {r.status_code}")
            return []

        root = ET.fromstring(r.content)
        root_tag = root.tag

        if 'sitemapindex' in root_tag:
            return [loc.text for loc in root.findall(".//ns:loc", NAMESPACE)]
        elif 'urlset' in root_tag:
            return [loc.text for loc in root.findall(".//ns:loc", NAMESPACE)]
        else:
            print(f"Unrecognized root tag: {root_tag}")
            return []
    except Exception as e:
        print(f"Error parsing sitemap {sitemap_url}: {e}")
        return []

def resolve_all_page_urls(start_url, rp):
    urls = fetch_sitemap_urls(start_url, rp)
    all_page_urls = []

    if not urls:
        return []

    for url in urls:
        if url.endswith('.xml'):
            nested = fetch_sitemap_urls(url, rp)
            print(f"  ➤ {len(nested)} URLs found in {url}")
            all_page_urls.extend(nested)
            time.sleep(1)
        else:
            all_page_urls.append(url)

    return all_page_urls

# === Text extraction ===
def get_clean_text(url, rp):
    if not can_fetch_url(rp, url):
        print(f"Blocked by robots.txt: {url}")
        return None
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            print(f"Failed to fetch {url}, status code {r.status_code}")
            return None

        soup = BeautifulSoup(r.content, "html.parser")

        for tag in soup(["script", "style", "header", "footer", "nav", "aside"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        return text
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

# === Save output ===
def save_to_json(url, text, index):
    filename = f"{index:04d}.json"
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump({"url": url, "text": text}, f, ensure_ascii=False, indent=2)

# === Run the script ===
def run_full_sitemap_crawl(sitemap_url):
    rp = fetch_robots_txt(sitemap_url)
    print(f"📥 Starting crawl for sitemap: {sitemap_url}")
    page_urls = resolve_all_page_urls(sitemap_url, rp)
    print(f"✅ Total page URLs collected: {len(page_urls)}")

    for i, url in enumerate(page_urls):
        print(f"[{i+1}/{len(page_urls)}] Processing: {url}")
        text = get_clean_text(url, rp)
        if text and len(text) > 300:
            save_to_json(url, text, i)
        else:
            print("Skipped (too short, empty, or disallowed)")
        time.sleep(1)

# === Entry point ===
if __name__ == "__main__":
    run_full_sitemap_crawl(SITEMAP_URL)
