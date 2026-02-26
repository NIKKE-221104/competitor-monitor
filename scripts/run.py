def atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", errors="ignore")
    tmp.replace(path)

def _style_block():
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

def _badge(status: str, count: int, err_count: int):
    # 숫자 “0” 대신 사람이 읽는 상태로 (요약 UX 핵심)
    if err_count > 0:
        return "<span class='badge'>오류</span>"
    if count > 0 and status == "changed":
        return f"<span class='badge'>변화 {count}건</span>"
    return "<span class='badge'>변화 없음</span>"

def write_index(sites, results, run_date):
    """
    results: 모든 타겟 결과를 담는 리스트
      item 예시:
      {
        "site_key": "...",
        "site_name": "...",
        "label": "...",
        "url": "...",
        "rel_dir": "assets/<site>/<date>",
        "changed": True/False,
        "error": True/False,
        "error_msg": "...",
      }
    """

    # 사이트별로 그룹핑
    by_site = {s["key"]: [] for s in sites}
    for r in results:
        by_site.setdefault(r["site_key"], []).append(r)

    # 사이트별 detail 페이지 먼저 생성
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

        lines.append(f"<h1>{site_name} <span class='muted'>({key})</span>{_badge('changed' if changed_items else 'unchanged', len(changed_items), len(err_items))}</h1>")
        lines.append(f"<p class='muted'>Last run: <code>{run_date}</code> · <a href='../index.html'>목록으로</a></p>")
        if url:
            lines.append(f"<p class='muted'>URL: <a href='{url}' target='_blank'>{url}</a></p>")

        # 오류 표시
        if err_items:
            lines.append("<div class='card'>")
            lines.append("<b>오류</b>")
            for e in err_items[:10]:
                msg = (e.get("error_msg") or "unknown").replace("<", "&lt;")
                lines.append(f"<div class='item'><code>{e['label']}</code> — <span class='muted'>{msg}</span></div>")
            lines.append("</div>")

        # 변경 없음
        if not changed_items and not err_items:
            lines.append("<div class='card'><div class='muted'>변화 없음</div></div>")
            atomic_write(sites_dir / f"{key}.html", "\n".join(lines))
            continue

        # 변경 목록
        if changed_items:
            lines.append("<div class='card'>")
            lines.append("<b>변경 목록</b>")
            for c in changed_items:
                rel = c["rel_dir"]
                label = c["label"]

                shot_top = f"{rel}/{label}_top.png"
                shot_full = f"{rel}/{label}.png"
                diff = f"{rel}/{label}_diff.html"

                # detail 페이지는 docs/sites/ 아래라서 ../ 붙여야 함
                lines.append("<div class='item'>")
                lines.append(f"<div><b>Target:</b> <code>{label}</code></div>")
                lines.append("<div class='links' style='margin-top:6px'>"
                             f"<a href='../{shot_top}'>Top</a>"
                             f"<a href='../{shot_full}'>Full</a>"
                             f"<a href='../{diff}'>Text diff</a>"
                             "</div>")
                lines.append("</div>")
            lines.append("</div>")

        atomic_write(sites_dir / f"{key}.html", "\n".join(lines))

    # index(요약) 생성
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
        lines.append(f"<div><b>{site_name}</b> <span class='muted'>({key})</span>{_badge('changed' if changed_cnt else 'unchanged', changed_cnt, err_cnt)}</div>")
        lines.append(f"<a href='sites/{key}.html'>상세보기</a>")
        lines.append("</div>")

        if url:
            lines.append(f"<div class='muted' style='margin-top:8px'>URL: <a href='{url}' target='_blank'>{url}</a></div>")

        if err_cnt:
            lines.append(f"<div class='muted' style='margin-top:10px'>오류 {err_cnt}건 (상세에서 확인)</div>")
        elif changed_cnt == 0:
            lines.append("<div class='muted' style='margin-top:10px'>변화 없음</div>")
        else:
            lines.append(f"<div class='muted' style='margin-top:10px'>변화 {changed_cnt}건</div>")

        lines.append("</div>")

    atomic_write(DOCS_DIR / "index.html", "\n".join(lines))
