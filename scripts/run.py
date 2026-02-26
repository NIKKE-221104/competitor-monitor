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


# ---------- utils ----------

def utc_date_str_kst() -> str:
    now_kst = datetime.now(timezone.utc) + timedelta(hours=9)
    return now_kst.strftime("%Y-%m-%d")


def sanitize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


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


def atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", errors="ignore")
    tmp.replace(path)


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

    with smtplib.SMTP(host, int(port), timeout=30) as server:
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


# ---------- diff ----------

def make_diff_html(old_text: str, new_text: str, title: str) -> str:
    hd = HtmlDiff(wrapcolumn=100)

    old_words = old_text.split(" ")
    new_words = new_text.split(" ")

    # 너무 길어지면 pages가 무거워져서 제한
    old_words = old_words[:3000]
    new_words = new_words[:3000]

    old_lines = [" ".join(old_words[i:i + 60]) for i in range(0, len(old_words), 60)]
    new_lines = [" ".join(new_words[i:i + 60]) for i in range(0, len(new_words), 60)]

    html = hd.make_file(
        old_lines,
        new_lines,
        fromdesc="before",
        todesc="after",
        context=True,
        numlines=2,
    )
    return html.replace("<title>HTML Diff</title>", f"<title>{title}</title>")


# ---------- html rendering ----------

def _style_block() -> str:
    return (
        "<style>"
        "body{font-family:system-ui,Arial;margin:24px;}"
        "h1{margin:0 0 8px 0;}"
        ".muted{color:#666;}"
        ".card{border:1px solid #ddd;border-radius:12px;padding:16px;margin:12px 0;}"
        ".badge{display:inline-block;min-width:24px;padding:2px 10px;border-radius:999px;"
        "background:#111;color:#fff;font-size:12px;vertical-align:middle;margin-left:8px;}"
        "code{background:#f5f5f5;padding:2px 6px;border-radius:6px;}"
        ".row{display:flex;gap:12px;align-items:center;flex-wrap:wrap;}"
        ".row a{margin-left:auto;}"
        ".item{margin-top:10px;padding-top:10px;border-top:1px dashed #ddd;}"
        ".links a{margin-right:10px;}"
        "</style>"
    )


def _badge(changed_cnt: int, err_cnt: int) -> str:
    if err_cnt > 0:
        return "<span class='badge'>오류</span>"
    if changed_cnt > 0:
        return f"<span class='badge'>변화 {changed_cnt}건</span>"
    return "<span class='badge'>변화 없음</span>"


def write_reports(sites: list[dict], results: list[dict], run_date: str):
    """
    results item:
      {
        "site_key": str,
        "site_name": str,
        "label": str,
        "url": str,
        "rel_dir": str,  # assets/<site>/<date>
        "changed": bool,
        "error": bool,
        "error_msg": str|None,
      }
    """
    # group by site
    by_site: dict[str, list[dict]] = {s["key"]: [] for s in sites}
    for r in results:
        by_site.setdefault(r["site_key"], []).append(r)

    # detail pages
    sites_dir = DOCS_DIR / "sites"
    sites_dir.mkdir(parents=True, exist_ok=True)

    for s in sites:
        key = s["key"]
        site_name = s.get("name", key)
        url = s.get("url", "")
        items = by_site.get(key, [])

        changed_items = [x for x in items if x.get("changed")]
        err_items = [x for x in items if x.get("error")]

        lines = []
        lines.append("<!doctype html>")
        lines.append("<meta charset='utf-8'>")
        lines.append(f"<title>{site_name} - Competitor Monitor</title>")
        lines.append(_style_block())

        lines.append(
            f"<h1>{site_name} <span class='muted'>({key})</span>"
            f"{_badge(len(changed_items), len(err_items))}</h1>"
        )
        lines.append(
            f"<p class='muted'>Last run: <code>{run_date}</code> · "
            f"<a href='../index.html'>목록으로</a></p>"
        )
        if url:
            lines.append(
                f"<p class='muted'>URL: <a href='{url}' target='_blank'>{url}</a></p>"
            )

        if err_items:
            lines.append("<div class='card'>")
            lines.append("<b>오류</b>")
            for e in err_items[:20]:
                msg = (e.get("error_msg") or "unknown").replace("<", "&lt;")
                lines.append(
                    f"<div class='item'><code>{e['label']}</code> — "
                    f"<span class='muted'>{msg}</span></div>"
                )
            lines.append("</div>")

        if not changed_items and not err_items:
            lines.append("<div class='card'><div class='muted'>변화 없음</div></div>")
            atomic_write(sites_dir / f"{key}.html", "\n".join(lines))
            continue

        if changed_items:
            lines.append("<div class='card'>")
            lines.append("<b>변경 목록</b>")
            for c in changed_items:
                rel = c["rel_dir"]
                label = c["label"]

                shot_top = f"{rel}/{label}_top.png"
                shot_full = f"{rel}/{label}.png"
                diff = f"{rel}/{label}_diff.html"

                lines.append("<div class='item'>")
                lines.append(f"<div><b>Target:</b> <code>{label}</code></div>")
                lines.append(
                    "<div class='links' style='margin-top:6px'>"
                    f"<a href='../{shot_top}'>Top</a>"
                    f"<a href='../{shot_full}'>Full</a>"
                    f"<a href='../{diff}'>Text diff</a>"
                    "</div>"
                )
                lines.append("</div>")
            lines.append("</div>")

        atomic_write(sites_dir / f"{key}.html", "\n".join(lines))

    # index summary
    lines = []
    lines.append("<!doctype html>")
    lines.append("<meta charset='utf-8'>")
    lines.append("<title>Competitor Monitor</title>")
    lines.append(_style_block())

    lines.append("<h1>Competitor Monitor</h1>")
    lines.append(f"<p class='muted'>Last run: <code>{run_date}</code></p>")
    lines.append("<h2>Sites</h2>")
    lines.append("<p class='muted'>요약에서 상태 확인 → 상세 페이지에서 스크린샷/디프 확인.</p>")

    for s in sites:
        key = s["key"]
        site_name = s.get("name", key)
        url = s.get("url", "")
        items = by_site.get(key, [])
        changed_cnt = sum(1 for x in items if x.get("changed"))
        err_cnt = sum(1 for x in items if x.get("error"))

        lines.append("<div class='card'>")
        lines.append("<div class='row'>")
        lines.append(
            f"<div><b>{site_name}</b> <span class='muted'>({key})</span>"
            f"{_badge(changed_cnt, err_cnt)}</div>"
        )
        lines.append(f"<a href='sites/{key}.html'>상세보기</a>")
        lines.append("</div>")

        if url:
            lines.append(
                f"<div class='muted' style='margin-top:8px'>URL: "
                f"<a href='{url}' target='_blank'>{url}</a></div>"
            )

        if err_cnt:
            lines.append(f"<div class='muted' style='margin-top:10px'>오류 {err_cnt}건 (상세에서 확인)</div>")
        elif changed_cnt == 0:
            lines.append("<div class='muted' style='margin-top:10px'>변화 없음</div>")
        else:
            lines.append(f"<div class='muted' style='margin-top:10px'>변화 {changed_cnt}건</div>")

        lines.append("</div>")

    atomic_write(DOCS_DIR / "index.html", "\n".join(lines))


# ---------- main ----------

def main():
    ensure_dirs()

    if not SITES_FILE.exists():
        print("sites.json 파일이 없어.")
        return

    sites = json.loads(SITES_FILE.read_text(encoding="utf-8"))
    state = load_json(STATE_FILE, default={"hashes": {}, "last_run": None})

    run_date = utc_date_str_kst()
    results: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        for site in sites:
            site_key = site["key"]
            site_name = site.get("name", site_key)

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

                # 기본 결과(항상 기록)
                result_item = {
                    "site_key": site_key,
                    "site_name": site_name,
                    "label": label,
                    "url": url,
                    "rel_dir": rel_dir,
                    "changed": False,
                    "error": False,
                    "error_msg": None,
                }

                # goto
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                except PWTimeoutError:
                    result_item["error"] = True
                    result_item["error_msg"] = "goto timeout"
                    results.append(result_item)
                    continue
                except Exception as e:
                    result_item["error"] = True
                    result_item["error_msg"] = f"goto error: {type(e).__name__}"
                    results.append(result_item)
                    continue

                try:
                    page.wait_for_timeout(1500)
                except Exception:
                    pass

                # screenshots
                try:
                    page.screenshot(path=str(shot_top_path), full_page=False)
                except Exception:
                    pass
                try:
                    page.screenshot(path=str(shot_path), full_page=True)
                except Exception:
                    pass

                # html content
                try:
                    html = page.content()
                except Exception as e:
                    result_item["error"] = True
                    result_item["error_msg"] = f"content error: {type(e).__name__}"
                    results.append(result_item)
                    continue

                html_path.write_text(html, encoding="utf-8", errors="ignore")

                text = html_to_text(html)
                new_hash = sha256(text)

                state_key = f"{site_key}::{label}"
                old_hash = state["hashes"].get(state_key)
                old_text = (
                    prev_txt_path.read_text(encoding="utf-8", errors="ignore")
                    if prev_txt_path.exists()
                    else ""
                )

                # 첫 실행: 기준만 저장 (변화 없음 처리)
                if old_hash is None:
                    state["hashes"][state_key] = new_hash
                    prev_txt_path.write_text(text, encoding="utf-8")
                    results.append(result_item)
                    continue

                changed = (old_hash != new_hash)
                if changed:
                    result_item["changed"] = True
                    diff_html = make_diff_html(old_text, text, f"{site_name} / {label}")
                    diff_path.write_text(diff_html, encoding="utf-8", errors="ignore")

                # 다음 비교를 위해 상태 갱신
                state["hashes"][state_key] = new_hash
                prev_txt_path.write_text(text, encoding="utf-8")

                results.append(result_item)

        context.close()
        browser.close()

    write_reports(sites, results, run_date)
    retention_cleanup()

    state["last_run"] = run_date
    save_json(STATE_FILE, state)

    changed_list = [r for r in results if r.get("changed")]
    if changed_list:
        subject = f"[Monitor] Changes detected: {len(changed_list)}"
        body_lines = [f"Run date: {run_date}", ""]
        for c in changed_list:
            body_lines.append(f"- {c['site_name']} ({c['site_key']}): {c['label']}")
            body_lines.append(f"  {c['url']}")
        body_lines.append("")
        body_lines.append("Check the report site on GitHub Pages (docs/index.html).")
        send_email(subject, "\n".join(body_lines))


if __name__ == "__main__":
    main()
