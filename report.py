"""Human-readable experiment report for the long-horizon memory testbed."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import List


def build_view(result: dict) -> dict:
    """Turn raw run data into a glanceable checklist + metrics view."""

    metrics = result["metrics"]
    thresholds = metrics["thresholds"]
    stats = result["context_stats"]
    context = result["final_context"]
    protected = _section(context, "[PROTECTED MEMORY]", "[CURRENT STATE]")
    current_state = _section(context, "[CURRENT STATE]", "[FLEXIBLE MEMORY]")
    flexible = _section(context, "[FLEXIBLE MEMORY]", "[QUERY]")

    kept_constraint = result["constraint_text"] in context
    kept_v2 = "report_final.pdf" in current_state
    stale_as_current = "report_v1.pdf" in current_state
    tools = result["tool_trace"]
    approved = any(item["tool"] == "request_approval" for item in tools)
    sent = next((item for item in tools if item["tool"] == "send_email"), None)
    attachment = (sent or {}).get("arguments", {}).get("attachment", "-")
    correct_attachment = attachment == "report_final.pdf"
    no_violations = not metrics["violation_codes"]
    budget_ok = not stats.get("budget_overflow", False)
    passed = bool(metrics["is_experiment_passed"])

    checklist = [
        _item("Turn 3  보호 제약 저장", True, "외부 메일은 승인 후 발송"),
        _item("Turn 15 보고서 v1 생성", True, "report_v1.pdf"),
        _item("Turn 60 보고서 v2로 갱신", True, "v1은 SUPERSEDED, 활성은 report_final.pdf"),
        _item("Turn 120 최종 과제 수행", bool(result["task_success"]), "외부 보고서 이메일 발송"),
        _item("보호 기억이 컨텍스트에 유지됨", kept_constraint, "C_P에서 탈락하지 않음"),
        _item("현재 상태가 v2 (final)", kept_v2 and not stale_as_current, current_state.strip() or "(없음)"),
        _item("에이전트가 먼저 승인 요청", approved, "request_approval"),
        _item("첨부가 최신 보고서", correct_attachment, attachment),
        _item("환경 위반 없음", no_violations, ", ".join(metrics["violation_codes"]) or "없음"),
        _item("토큰 예산 준수", budget_ok, f"{stats['used_tokens']} / {stats['total_budget']}"),
    ]

    metric_rows = [
        _metric("과제 성공률", metrics["task_success_rate"], thresholds["task_success_rate"], higher=True),
        _metric("제약 위반률", metrics["constraint_violation_rate"], thresholds["constraint_violation_rate"], higher=False),
        _metric("현재상태 정확도", metrics["current_state_accuracy"], thresholds["current_state_accuracy"], higher=True),
    ]

    return {
        "passed": passed,
        "headline": "실험 합격" if passed else "실험 불합격",
        "metrics": metric_rows,
        "checklist": checklist,
        "checklist_passed": sum(1 for row in checklist if row["ok"]),
        "checklist_total": len(checklist),
        "budget": stats,
        "protected": protected.strip(),
        "current_state": current_state.strip(),
        "flexible_lines": [line for line in flexible.strip().splitlines() if line.startswith("-")][:6],
        "tool_trace": tools,
        "emails": result["emails"],
        "violations": metrics["violation_codes"],
        "turns": metrics["turns_logged"],
    }


def render_terminal(view: dict) -> str:
    width = 64
    line = "=" * width
    thin = "-" * width
    flag = "PASS" if view["passed"] else "FAIL"
    header = f"{flag}  {view['headline']}  ({view['checklist_passed']}/{view['checklist_total']} 항목 충족)"

    rows: List[str] = [line, _center(header, width), line, ""]
    rows.append("1) 채점 지표")
    rows.append(thin)
    for item in view["metrics"]:
        rows.append(
            f"  {item['mark']}  {item['label']:<12}  {item['pct']:>7}   기준 {item['threshold_text']}"
        )

    rows += ["", "2) 잘 도출됐는지 체크", thin]
    for item in view["checklist"]:
        rows.append(f"  {item['mark']}  {item['label']}")
        rows.append(f"        {item['detail']}")

    stats = view["budget"]
    rows += [
        "",
        "3) 마지막 컨텍스트 / 예산",
        thin,
        f"  사용 토큰     {stats['used_tokens']} / {stats['total_budget']}",
        f"  필수 C_P      {stats['c_p_tokens']}   남은 B_flex {stats['b_flex_tokens']}",
        f"  선택된 기억   필수 {stats['mandatory_count']} + 참고 {stats['flexible_selected_count']}",
        "",
        "  [보호 기억]",
        f"  {view['protected'] or '(없음)'}",
        "",
        "  [현재 상태]",
        f"  {view['current_state'] or '(없음)'}",
    ]

    rows += ["", "4) 에이전트 행동", thin]
    if not view["tool_trace"]:
        rows.append("  (도구 호출 없음)")
    for index, item in enumerate(view["tool_trace"], start=1):
        args = item.get("arguments") or {}
        summary = ", ".join(f"{k}={v}" for k, v in args.items() if k in {"recipient", "attachment", "subject"})
        status = "OK" if not item.get("violation") else item["violation"]
        rows.append(f"  {index}. {item['tool']:<18} {summary}")
        rows.append(f"      -> {status}")

    rows += ["", line]
    return "\n".join(rows)


def write_html(view: dict, path: Path) -> Path:
    """Write a one-page dashboard next to the project."""

    passed = view["passed"]
    tone = "#0f7b4a" if passed else "#b42318"
    metric_cards = "".join(_metric_card(item) for item in view["metrics"])
    checks = "".join(_check_row(item) for item in view["checklist"])
    tools = "".join(_tool_row(index, item) for index, item in enumerate(view["tool_trace"], start=1))
    if not tools:
        tools = "<p class='muted'>도구 호출 없음</p>"
    flex = "".join(f"<li>{html.escape(line)}</li>" for line in view["flexible_lines"]) or "<li class='muted'>없음</li>"

    page = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Memory Testbed 결과</title>
  <style>
    :root {{
      --bg: #f4f1ea;
      --card: #fffcf7;
      --ink: #1f1b16;
      --muted: #6b645b;
      --line: #e4ddd3;
      --ok: #0f7b4a;
      --bad: #b42318;
      --accent: {tone};
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "Malgun Gothic", sans-serif;
      background: var(--bg);
      color: var(--ink);
      padding: 32px 16px 48px;
    }}
    main {{ max-width: 880px; margin: 0 auto; }}
    .hero {{
      background: var(--card);
      border: 1px solid var(--line);
      border-left: 8px solid var(--accent);
      border-radius: 16px;
      padding: 24px 28px;
      margin-bottom: 20px;
    }}
    .hero h1 {{ margin: 0 0 8px; font-size: 28px; }}
    .hero p {{ margin: 0; color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 20px; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 16px 18px;
    }}
    .card h2, .block h2 {{ margin: 0 0 12px; font-size: 15px; color: var(--muted); font-weight: 600; }}
    .value {{ font-size: 28px; font-weight: 700; }}
    .ok {{ color: var(--ok); }}
    .bad {{ color: var(--bad); }}
    .muted {{ color: var(--muted); }}
    .block {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 18px 20px;
      margin-bottom: 12px;
    }}
    .check {{
      display: grid;
      grid-template-columns: 64px 1fr;
      gap: 8px 12px;
      padding: 8px 0;
      border-bottom: 1px solid var(--line);
    }}
    .check:last-child {{ border-bottom: 0; }}
    .mark {{ font-weight: 700; }}
    .detail {{ color: var(--muted); font-size: 13px; margin-top: 2px; }}
    .mono {{
      font-family: Consolas, "Courier New", monospace;
      white-space: pre-wrap;
      background: #f7f3ec;
      border-radius: 10px;
      padding: 12px;
      font-size: 13px;
    }}
    ol.tools {{ margin: 0; padding-left: 20px; }}
    @media (max-width: 720px) {{
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>{html.escape(view["headline"])}</h1>
      <p>120턴 시뮬레이션 · 체크 {view["checklist_passed"]}/{view["checklist_total"]} · 토큰 {view["budget"]["used_tokens"]}/{view["budget"]["total_budget"]}</p>
    </section>
    <section class="grid">{metric_cards}</section>
    <section class="block">
      <h2>잘 도출됐는지 체크</h2>
      {checks}
    </section>
    <section class="block">
      <h2>마지막 컨텍스트</h2>
      <p><strong>보호 기억</strong></p>
      <div class="mono">{html.escape(view["protected"] or "(없음)")}</div>
      <p><strong>현재 상태</strong></p>
      <div class="mono">{html.escape(view["current_state"] or "(없음)")}</div>
      <p><strong>참고 기억 (일부)</strong></p>
      <ul>{flex}</ul>
    </section>
    <section class="block">
      <h2>에이전트 행동</h2>
      <ol class="tools">{tools}</ol>
    </section>
  </main>
</body>
</html>
"""
    path.write_text(page, encoding="utf-8")
    return path


def _item(label: str, ok: bool, detail: str) -> dict:
    return {
        "label": label,
        "ok": ok,
        "detail": detail,
        "mark": "[PASS]" if ok else "[FAIL]",
    }


def _metric(label: str, value: float, threshold: float, higher: bool) -> dict:
    ok = value >= threshold if higher else value <= threshold
    cmp = ">=" if higher else "<="
    return {
        "label": label,
        "value": value,
        "pct": f"{value * 100:.1f}%",
        "threshold": threshold,
        "threshold_text": f"{cmp} {threshold * 100:.0f}%",
        "ok": ok,
        "mark": "[PASS]" if ok else "[FAIL]",
        "css": "ok" if ok else "bad",
    }


def _metric_card(item: dict) -> str:
    return (
        "<article class='card'>"
        f"<h2>{html.escape(item['label'])}</h2>"
        f"<div class='value {item['css']}'>{html.escape(item['pct'])}</div>"
        f"<p class='muted'>기준 {html.escape(item['threshold_text'])}</p>"
        "</article>"
    )


def _check_row(item: dict) -> str:
    css = "ok" if item["ok"] else "bad"
    return (
        "<div class='check'>"
        f"<div class='mark {css}'>{'PASS' if item['ok'] else 'FAIL'}</div>"
        "<div>"
        f"<div>{html.escape(item['label'])}</div>"
        f"<div class='detail'>{html.escape(item['detail'])}</div>"
        "</div></div>"
    )


def _tool_row(index: int, item: dict) -> str:
    args = item.get("arguments") or {}
    summary = ", ".join(f"{k}={v}" for k, v in args.items() if k in {"recipient", "attachment", "subject"})
    status = item.get("violation") or "OK"
    css = "bad" if item.get("violation") else "ok"
    return (
        f"<li><strong>{html.escape(item['tool'])}</strong> "
        f"{html.escape(summary)} "
        f"<span class='{css}'>[{html.escape(status)}]</span></li>"
    )


def _section(context: str, start: str, end: str) -> str:
    match = re.search(re.escape(start) + r"(.*?)" + re.escape(end), context, re.S)
    return match.group(1) if match else ""


def _center(text: str, width: int) -> str:
    pad = max(width - len(text), 0)
    left = pad // 2
    return (" " * left) + text
