import os
import json
import re
import hashlib
import shutil
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from pathlib import Path
from bs4 import BeautifulSoup
from difflib import HtmlDiff

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

ROOT = Path(__file__).resolve().parents[1]
SITES_FILE = ROOT / "sites.json"
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
STATE_FILE = DATA_DIR / "state.json"
DOCS_DIR = ROOT / "docs"
ASSETS_DIR = DOCS_DIR / "assets"

RETENTION_DAYS = 30  # repo 용량 관리용

def utc_date_str_kst():
    # UTC 기준 시간에 9시간을 더해 KST로 깔끔하게 변환
    now_kst = datetime.now(timezone.utc) + timedelta(hours=9)
    return now_kst.strftime("%Y-%m-%d")

def sanitize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text

def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return sanitize_text(text)

def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()

def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default

def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def send_email(subject: str, body: str):
    host = os.getenv("SMTP_HOST", "").strip()
    port = os.getenv("SMTP_PORT", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    pw = os.getenv("SMTP_PASS", "").strip()
    to_addr = os.getenv("ALERT_TO", "").strip()

    if not (host and port and user and pw and to_addr):
        return

    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr

    port_i = int(port)
    with smtplib.SMTP(host, port_i, timeout=30) as server:
        server.ehlo()
        try:
            server.starttls()
            server.ehlo()
        except Exception:
            pass
        server.login(user, pw)
        server.sendmail(user, [to_addr], msg.as_string())

def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

def retention_cleanup():
    if not ASSETS_DIR.exists():
        return
    cutoff = datetime.utcnow().timestamp() - (RETENTION_DAYS * 86400)

    for site_dir in ASSETS_DIR.iterdir():
        if not site_dir.is_dir():
            continue
        for day_dir in site_dir.iterdir():
            if not day_dir.is_dir():
                continue
            try:
                dt = datetime.strptime(day_dir.name, "%Y-%m-%d")
                if dt.timestamp() < cutoff:
                    shutil.rmtree(day_dir, ignore_errors=True)
            except Exception:
                continue

# write_index 함수를 독립적인 스코프로 분리 및 들여쓰기 논리 수정
def write_index(sites, changes, run_date):
    by_site = {s["key"]: [] for s in sites}
    
    for c in changes:
        by_site.setdefault(c["site_key"], []).append(c)

    lines = []
    lines.append("<!doctype html>")
    lines.append("<meta charset='utf-8'>")
    lines.append("<title>Competitor Monitor</title>")
    lines.append(
    "<style>"
    "body{font-family:system-ui,Arial;margin:24px;}"
    "h1{margin:0 0 8px 0;}"
    ".muted{color:#666;}"
    ".card{border:1px solid #ddd;border-radius:12px;padding:16px;margin:12px 0;}"
    ".badge{display:inline-block;min-width:24px;padding:2px 8px;border-radius:999px;"
    "background:#111;color:#fff;font-size:12px;vertical-align:middle;margin-left:8px;}"
    "code{background:#f5f5f5;padding:2px 6px;border-radius:6px;}"
    "summary{cursor:pointer;font-weight:700;}"
    ".item{margin-top:10px;padding-top:10px;border-top:1px dashed #ddd;}"
    ".links a{margin-right:10px;}"
    "</style>"
    )

    lines.append("<h1>Competitor Monitor</h1>")
    lines.append(f"<p class='muted'>Last run: <code>{run_date}</code></p>")
    lines.append("<h2>Sites</h2>")
    lines.append("<p class='muted'>변동 0이어도 목록에 표시됨. 사이트를 눌러서 세부 확인.</p>")

    for s in sites:
        key = s["key"]
        site_name = s.get("name", key)
        url = s.get("url", "")
        items = by_site.get(key, [])
        cnt = len(items)

        # 들여쓰기 교정: 각 사이트마다 카드가 그려지도록 루프 내부로 이동
        lines.append("<div class='card'>")
        lines.append(
            f"<details {'open' if cnt else ''}>"
            f"<summary>{site_name} <span class='muted'>({key})</span>"
            f"<span class='badge'>{cnt}</span></summary>"
        )
        if url:
            lines.append(f"<div class='muted' style='margin-top:8px'>URL: <a href='{url}' target='_blank'>{url}</a></div>")

        if cnt == 0:
            lines.append("<div class='muted' style='margin-top:10px'>No changes detected (based on text hash).</div>")
        else:
            for c in items:
                rel = c["rel_dir"]
                label = c["label"]

                shot_top = f"{rel}/{label}_top.png"
                shot_full = f"{rel}/{label}.png"
                diff = f"{rel}/{label}_diff.html"

                lines.append("<div class='item'>")
                lines.append(f"<div><b>Target:</b> <code>{label}</code></div>")
                lines.append("<div class='links' style='margin-top:6px'>"
                             f"<a href='{shot_top}'>Screenshot</a>"
                             f"<a href='{shot_full}'>Full</a>"
                             f"<a href='{diff}'>Text diff</a>"
                             "</div>")
                lines.append("</div>")

        lines.append("</details>")
        lines.append("</div>")

    (DOCS_DIR / "index.html").write_text("\n".join(lines), encoding="utf-8")

def make_diff_html(old_text: str, new_text: str, title: str) -> str:
    hd = HtmlDiff(wrapcolumn=100)
    old_lines = old_text.split(" ")
    new_lines = new_text.split(" ")
    old_lines = [" ".join(old_lines[i:i+60]) for i in range(0, min(len(old_lines), 3000), 60)]
    new_lines = [" ".join(new_lines[i:i+60]) for i in range(0, min(len(new_lines), 3000), 60)]
    return hd.make_file(old_lines, new_lines, fromdesc="before", todesc="after", context=True, numlines=2).replace("<title>HTML Diff</title>", f"<title>{title}</title>")

def main():
    ensure_dirs()
    
    if not SITES_FILE.exists():
        print("sites.json 파일이 없어.")
        return
        
    sites = json.loads(SITES_FILE.read_text(encoding="utf-8"))
    state = load_json(STATE_FILE, default={"hashes": {}, "last_run": None})

    run_date = utc_date_str_kst()
    changes = []
    changed_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        for site in sites:
            site_key = site["key"]
            site_name = site.get("name", site_key) # 방어적 코드 추가
            
            for t in site.get("targets", []):
                label = t["label"]
                url = t["url"]

                day_dir = ASSETS_DIR / site_key / run_date
                day_dir.mkdir(parents=True, exist_ok=True)
                rel_dir = f"assets/{site_key}/{run_date}"

                shot_path = day_dir / f"{label}.png"
                shot_top_path = day_dir / f"{label}_top.png"
                diff_path = day_dir / f"{label}_diff.html"
                html_path = day_dir / f"{label}.html"

                cache_txt_dir = CACHE_DIR / site_key
                cache_txt_dir.mkdir(parents=True, exist_ok=True)
                prev_txt_path = cache_txt_dir / f"{label}.txt"

                try:
                    page.goto(url, wait_until="networkidle", timeout=60000)
                except PWTimeoutError:
                    pass
                except Exception:
                    pass

                try:
                    page.wait_for_timeout(1500)
                except Exception:
                    pass

                try:
                    page.screenshot(path=str(shot_top_path), full_page=False)
                except Exception:
                    pass

                try:
                    page.screenshot(path=str(shot_path), full_page=True)
                except Exception:
                    pass

                try:
                    html = page.content()
                except Exception:
                    html = ""
                html_path.write_text(html, encoding="utf-8", errors="ignore")

                text = html_to_text(html)
                new_hash = sha256(text)

                key = f"{site_key}::{label}"
                old_hash = state["hashes"].get(key)
                old_text = prev_txt_path.read_text(encoding="utf-8", errors="ignore") if prev_txt_path.exists() else ""

                changed = (old_hash is not None and old_hash != new_hash)
                
                if old_hash is None:
                    state["hashes"][key] = new_hash
                    prev_txt_path.write_text(text, encoding="utf-8")
                    continue

                if changed:
                    changed_count += 1
                    diff_html = make_diff_html(old_text, text, f"{site_name} / {label}")
                    diff_path.write_text(diff_html, encoding="utf-8", errors="ignore")

                    changes.append({
                        "site_key": site_key,
                        "site_name": site_name,
                        "label": label,
                        "url": url,
                        "rel_dir": rel_dir
                    })

                    state["hashes"][key] = new_hash
                    prev_txt_path.write_text(text, encoding="utf-8")

        context.close()
        browser.close()

    write_index(sites, changes, run_date)
    retention_cleanup()

    state["last_run"] = run_date
    save_json(STATE_FILE, state)

    if changes:
        subject = f"[Monitor] Changes detected: {len(changes)}"
        body_lines = [f"Run date: {run_date}", ""]
        for c in changes:
            body_lines.append(f"- {c['site_name']} ({c['site_key']}): {c['label']}")
            body_lines.append(f"  {c['url']}")
        body_lines.append("")
        body_lines.append("Check the report site on GitHub Pages (docs/index.html).")
        send_email(subject, "\n".join(body_lines))

if __name__ == "__main__":
    main()
