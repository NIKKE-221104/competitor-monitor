import os
import json
import re
import hashlib
import shutil
import smtplib
from datetime import datetime, timezone
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

RETENTION_DAYS = 30  # repo 용량 관리용. 필요하면 늘려.

def utc_date_str_kst():
    # GitHub Actions는 UTC 기준이라, 날짜 폴더를 "KST 기준 날짜"로 맞추고 싶으면 여기서 +9h
    # 단순 처리: UTC+9 offset 적용
    now_utc = datetime.now(timezone.utc)
    now_kst = now_utc.astimezone(timezone.utc).replace(tzinfo=None)  # placeholder
    # 정확히 KST로 하려면 zoneinfo가 깔끔하지만, 여기선 폴더명용이라 단순 +9h로 처리
    now_kst = now_utc.replace(tzinfo=None)  # naive UTC
    now_kst = now_kst.replace()  # no-op
    # 그냥 UTC 날짜로 써도 상관 없으면 아래 한 줄로 바꿔:
    # return now_utc.strftime("%Y-%m-%d")
    # 여기선 KST 날짜 흉내: UTC시간 +9시간
    from datetime import timedelta
    now_kst = now_utc + timedelta(hours=9)
    return now_kst.strftime("%Y-%m-%d")

def sanitize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text

def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    # 너무 길어지는 footer/nav도 섞이긴 하지만 1차는 전체 텍스트로 간다.
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

    # Secrets 미설정이면 그냥 스킵
    if not (host and port and user and pw and to_addr):
        return

    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr

    port_i = int(port)
    with smtplib.SMTP(host, port_i, timeout=30) as server:
        server.ehlo()
        # 대부분 587은 STARTTLS, 465는 SSL인데 환경마다 다르니
        # 1차는 587(STARTTLS) 가정. 465 쓰면 아래 로직 바꿔야 함.
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
    # docs/assets/<site>/<YYYY-MM-DD>/ 폴더 기준으로 오래된 날짜 폴더 삭제
    if not ASSETS_DIR.exists():
        return
    cutoff = datetime.utcnow().timestamp() - (RETENTION_DAYS * 86400)

    for site_dir in ASSETS_DIR.iterdir():
        if not site_dir.is_dir():
            continue
        for day_dir in site_dir.iterdir():
            if not day_dir.is_dir():
                continue
            # 폴더명 파싱 실패하면 건드리지 않음
            try:
                dt = datetime.strptime(day_dir.name, "%Y-%m-%d")
                if dt.timestamp() < cutoff:
                    shutil.rmtree(day_dir, ignore_errors=True)
            except Exception:
                continue

def write_index(changes, run_date):
    # 단순하지만 쓸만한 index 생성
    lines = []
    lines.append("<!doctype html><meta charset='utf-8'>")
    lines.append("<title>Competitor Monitor</title>")
    lines.append("<style>body{font-family:system-ui,Arial;margin:24px} .card{border:1px solid #ddd;border-radius:12px;padding:16px;margin:12px 0} .muted{color:#666} code{background:#f5f5f5;padding:2px 6px;border-radius:6px} .links{margin-top:10px} .links a{margin-right:10px}</style>")
    lines.append(f"<h1>Competitor Monitor</h1>")
    lines.append(f"<p class='muted'>Last run: <code>{run_date}</code></p>")
    lines.append("<h2>Latest changes</h2>")

    if not changes:
        lines.append("<p>No changes detected (based on text hash).</p>")
    else:
        for c in changes:
            lines.append("<div class='card'>")
            lines.append(f"<div><b>{c['site_name']}</b> <span class='muted'>({c['site_key']})</span></div>")
            lines.append(f"<div class='muted'>Target: <code>{c['label']}</code></div>")
            lines.append(f"<div class='muted'>URL: <a href='{c['url']}' target='_blank'>{c['url']}</a></div>")
            # links
            rel = c["rel_dir"]
            shot_top = f"{rel}/{c['label']}_top.png"
            shot_full = f"{rel}/{c['label']}.png"
            diff = f"{rel}/{c['label']}_diff.html"

            lines.append("<div class='links'>")
            link_html = (
                f"<a href='{shot_top}'>Screenshot</a> · "
                f"<a href='{shot_full}'>Full</a> · "
                f"<a href='{diff}'>Text diff</a>"
            )
            lines.append(link_html)
            lines.append("</div>")
            lines.append("</div>")

    (DOCS_DIR / "index.html").write_text("\n".join(lines), encoding="utf-8")

def make_diff_html(old_text: str, new_text: str, title: str) -> str:
    hd = HtmlDiff(wrapcolumn=100)
    # 너무 길면 페이지가 무거워지니까 줄 단위로 적당히 자름
    old_lines = old_text.split(" ")
    new_lines = new_text.split(" ")
    old_lines = [" ".join(old_lines[i:i+60]) for i in range(0, min(len(old_lines), 3000), 60)]
    new_lines = [" ".join(new_lines[i:i+60]) for i in range(0, min(len(new_lines), 3000), 60)]
    return hd.make_file(old_lines, new_lines, fromdesc="before", todesc="after", context=True, numlines=2).replace("<title>HTML Diff</title>", f"<title>{title}</title>")

def main():
    ensure_dirs()
    sites = json.loads(SITES_FILE.read_text(encoding="utf-8"))
    state = load_json(STATE_FILE, default={"hashes": {}, "last_run": None})

    run_date = utc_date_str_kst()  # 날짜 폴더명
    changes = []
    changed_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        for site in sites:
            site_key = site["key"]
            site_name = site["name"]
            for t in site["targets"]:
                label = t["label"]
                url = t["url"]

                # 저장 경로
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

                # 페이지 로드 + 풀페이지 스샷
                try:
                    page.goto(url, wait_until="networkidle", timeout=60000)
                except PWTimeoutError:
                    # 타임아웃이어도 스샷/HTML은 최대한 남긴다
                    pass
                except Exception:
                    pass

                # 쿠키 배너 같은 게 가려도 일단 1차는 무시. 필요하면 selector 클릭 로직 추가하면 됨.
                try:
                    page.wait_for_timeout(1500)
                except Exception:
                    pass

                # 스크린샷(풀페이지)
                # 1) top(첫 화면) 스샷
                try:
                    page.screenshot(path=str(shot_top_path), full_page=False)
                except Exception:
                    pass

                # 2) full(전체) 스샷
                try:
                    page.screenshot(path=str(shot_path), full_page=True)
                except Exception:
                    pass

                # HTML 저장 + 텍스트 추출
                try:
                    html = page.content()
                except Exception:
                    html = ""
                html_path.write_text(html, encoding="utf-8", errors="ignore")

                text = html_to_text(html)
                new_hash = sha256(text)

                # 이전값
                key = f"{site_key}::{label}"
                old_hash = state["hashes"].get(key)
                old_text = prev_txt_path.read_text(encoding="utf-8", errors="ignore") if prev_txt_path.exists() else ""

                changed = (old_hash is not None and old_hash != new_hash)
                # 최초 실행은 baseline이니까 변경으로 치지 않음(원하면 changed=True로 바꿔도 됨)
                if old_hash is None:
                    state["hashes"][key] = new_hash
                    prev_txt_path.write_text(text, encoding="utf-8")
                    continue

                if changed:
                    changed_count += 1
                    # diff 생성
                    diff_html = make_diff_html(old_text, text, f"{site_name} / {label}")
                    diff_path.write_text(diff_html, encoding="utf-8", errors="ignore")

                    changes.append({
                        "site_key": site_key,
                        "site_name": site_name,
                        "label": label,
                        "url": url,
                        "rel_dir": rel_dir
                    })

                    # 상태 업데이트
                    state["hashes"][key] = new_hash
                    prev_txt_path.write_text(text, encoding="utf-8")

        context.close()
        browser.close()

    # 인덱스 갱신
    write_index(changes, run_date)

    # 보관정책 적용
    retention_cleanup()

    # 상태 저장
    state["last_run"] = run_date
    save_json(STATE_FILE, state)

    # 알림(메일)
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
