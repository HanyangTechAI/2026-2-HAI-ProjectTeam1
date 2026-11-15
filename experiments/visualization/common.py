"""Shared export, provenance, and gallery helpers (no model/API calls)."""

from __future__ import annotations

import csv
import hashlib
import html
import json
from pathlib import Path
import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from cycler import cycler

COLORS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#444444"]
STRATEGIES = ["proposed", "sliding_window", "vector_top_k", "utility_per_token"]
LABELS = ["Proposed", "Sliding Window", "Vector Top-k", "Utility-per-Token"]
CATEGORY_LABELS = {
    "main": "Main 본실험",
    "preliminary": "초기 파일럿·실행 검증",
    "followups": "Ablation·예산·장기 기억",
    "criticality": "Criticality·Packing·Safety Gate",
    "audits": "감사·오프라인 점검",
}


def configure():
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10,
        "axes.titlesize": 11, "axes.labelsize": 10,
        "legend.fontsize": 9, "figure.facecolor": "white",
        "axes.facecolor": "white", "axes.prop_cycle": cycler(color=COLORS),
        "svg.fonttype": "none", "svg.hashsalt": "hai-experiments-20261115",
        "savefig.facecolor": "white", "axes.unicode_minus": True,
    })


def vector_heatmap(ax, values, vmin, vmax, cmap, equal=False):
    """Draw individually editable SVG cells; imshow would embed a bitmap."""
    array = np.asarray(values)
    height, width = array.shape
    mesh = ax.pcolormesh(np.arange(width + 1) - .5, np.arange(height + 1) - .5,
                         array, shading="flat", vmin=vmin, vmax=vmax,
                         cmap=cmap, rasterized=False)
    ax.set_xlim(-.5, width-.5)
    ax.set_ylim(height-.5, -.5)
    if equal:
        ax.set_aspect("equal")
    ax._archive_heatmap = True
    return mesh


class PlotContext:
    def __init__(self, root: Path, output: Path):
        self.root, self.output = root, output
        self.records = []
        for name in ("svg", "previews", "data"):
            (output / name).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def style(ax):
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", color="#dddddd", linewidth=0.6, alpha=0.65)
        ax.set_axisbelow(True)

    def save(self, fig, chart_id, title, category, sources, note, data):
        if any(r["id"] == chart_id for r in self.records):
            raise ValueError(f"Duplicate chart ID: {chart_id}")
        if not data:
            raise ValueError(f"No underlying data: {chart_id}")
        for source in sources:
            if not (self.root / source).is_file():
                raise FileNotFoundError(source)
        fig.tight_layout(pad=1.7)
        fig.suptitle(title, y=1.025, fontsize=14, fontweight="bold")
        footer = f"{chart_id} | " + note
        # A separate artist outside the axes avoids covering legends or labels.
        wrap_width = max(90, int(fig.get_figwidth() * 14))
        fig.text(0.015, -0.025, textwrap.fill(footer, wrap_width), va="top",
                 ha="left", fontsize=8, color="#555555")
        svg = f"svg/{chart_id}.svg"
        preview = f"previews/{chart_id}.png"
        fig.savefig(self.output / svg, bbox_inches="tight", metadata={
            "Date": None, "Title": title,
            "Description": note + " Sources: " + "; ".join(sources),
        })
        # Matplotlib pads multiline SVG paths with trailing spaces; keep diffs clean.
        svg_path = self.output / svg
        svg_path.write_text("\n".join(line.rstrip() for line in
                            svg_path.read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8")
        fig.savefig(self.output / preview, bbox_inches="tight", dpi=115)
        plt.close(fig)
        fields = list(dict.fromkeys(k for row in data for k in row))
        csv_path = f"data/{chart_id}.csv"
        with (self.output / csv_path).open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fields)
            writer.writeheader()
            writer.writerows(data)
        record = dict(id=chart_id, title=title, category=category,
                      sources=sources, note=note, svg=svg, preview=preview,
                      data=csv_path, plotted_rows=len(data))
        self.records.append(record)
        print(f"[{len(self.records):02d}] {chart_id}", flush=True)

    def write_index(self):
        reports = sorted((self.root / "docs/experiments").glob("reports/*.md"))
        reports += sorted((self.root / "docs/experiments").glob("audits/*.md"))
        sources = sorted({p for r in self.records for p in r["sources"]})
        coverage = []
        for p in reports:
            if p.name == "README.md":
                continue
            relative = p.relative_to(self.root).as_posix()
            matches = [r["id"] for r in self.records if relative in r["sources"]]
            coverage.append(dict(source=relative, charts=matches,
                                 status="visualized" if matches else "not_visualized"))
        manifest = {
            "as_of": "2026-11-15", "engine": "matplotlib",
            "chart_count": len(self.records), "charts": self.records,
            "coverage": coverage,
            "sources": [{"path": p, "sha256": hashlib.sha256((self.root / p).read_bytes()).hexdigest()}
                        for p in sources],
            "limitations": [
                "Missing run logs were not reconstructed. Report tables are explicitly identified.",
                "Existing confidence intervals are preserved; no new intervals are inferred from aggregates.",
                "Main phases are subsets of the final Main experiment, not independent extra runs.",
                "Offline selection, scripted safety gates, smoke tests, and LLM outcomes are separate experiments.",
                "No model calls, evaluator changes, or edits to original experiment data were made.",
            ],
        }
        (self.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_readme(coverage)
        self._write_gallery()
        return manifest

    def _write_readme(self, coverage):
        text = ["# 전체 실험 matplotlib 시각화 · 2026-11-15", "",
                f"총 **{len(self.records)}개 SVG**. [미리보기 갤러리](index.html)에서 그림을 고르고 SVG를 열면 됩니다.", "",
                "- `svg/`: 확대해도 선명한 SVG. 글자와 도형을 편집할 수 있습니다.",
                "- `previews/`: 빠르게 확인하는 PNG 미리보기.",
                "- `data/`: 각 그림에 사용한 수치 CSV. 빈 값은 미측정/해당 없음이며 0으로 바꾸지 않았습니다.",
                "- `manifest.json`: 그림별 출처·주의점·원본 SHA-256 및 보고서 포함 여부.", "",
                "## 해석과 수정", "",
                "원본 JSON/CSV가 있는 경우 직접 읽고, 로컬 원본 로그가 없는 실험은 보고서 표의 수치를 코드에 명시했습니다. "
                "보고서 기반 그림의 주석과 출처를 함께 확인하세요. 오프라인 선택·scripted gate·smoke 결과는 LLM 성능과 구분했습니다.", "",
                "신뢰구간은 저장된 값 또는 보고서에 명시된 값만 표시했습니다. Main의 각 점은 40개 기본 과제 × 2회이며, "
                "반복·Budget·Horizon을 독립 과제로 세면 안 됩니다. Main 차수별 보고서는 최종 Main의 부분집합입니다. "
                "실패 태그는 겹칠 수 있어 합계가 실패 실행 수와 일치하지 않을 수 있습니다.", "",
                "Main의 answer exact match·F1은 전체 집계가 null이어서 그리지 않았습니다. 입력 token 집계의 `input_tokens`는 "
                "실행별 최대 단일 Context 크기입니다. 누적 provider 입력량과 구분해 별도 그림으로 표시했습니다.", "",
                "그림의 영문 표기는 폰트 호환성과 논문/발표 편집을 위한 것입니다. 수정 요청 시 그림 ID를 알려주면 됩니다. "
                "SVG의 글자는 텍스트로 유지되며 DejaVu Sans를 사용합니다.", "",
                "## 다시 생성", "", "저장소 루트의 PowerShell에서:", "", "```powershell",
                ".\\.venv\\Scripts\\python.exe -m pip install -r experiments/visualization/requirements.txt",
                ".\\.venv\\Scripts\\python.exe experiments/plot_all_results.py", "```", "",
                "코드는 `experiments/visualization/`에 있습니다. 출력 경로는 `--output`으로 지정할 수 있습니다. "
                "`--groups main preliminary followups criticality audits` 중 일부만 지정해 골라 생성할 수도 있습니다.", "",
                "전체 ZIP의 `reproduce/`에는 생성 코드와 사용한 집계·보고서가 같은 상대 경로로 들어 있습니다. "
                "해당 폴더에서 Python 환경을 준비하면 원본 저장소 없이도 다시 생성할 수 있습니다.", ""]
        for category, label in CATEGORY_LABELS.items():
            rows = [r for r in self.records if r["category"] == category]
            if not rows:
                continue
            text += [f"## {label}", "", "| 그림 ID | 내용 | 파일 |", "|---|---|---|"]
            for r in rows:
                text.append(f"| `{r['id']}` | {r['title']} | [SVG]({r['svg']}) · [CSV]({r['data']}) |")
            text.append("")
        text += ["## 보고서 포함 여부", "", "| 원본 보고서 | 그림 ID |", "|---|---|"]
        for item in coverage:
            label = Path(item["source"]).name
            ids = ", ".join(f"`{x}`" for x in item["charts"]) or "수치 시각화 없음"
            text.append(f"| {label} | {ids} |")
        (self.output / "README.md").write_text("\n".join(text) + "\n", encoding="utf-8")

    def _write_gallery(self):
        esc = html.escape
        cards = []
        for r in self.records:
            source_links = " · ".join(
                f'<a href="../../../{esc(p)}">{esc(Path(p).name)}</a>' for p in r["sources"])
            search = esc(" ".join([r["id"], r["title"], r["category"], r["note"]]))
            cards.append(f'''<article data-category="{esc(r['category'])}" data-search="{search}">
              <a href="{esc(r['svg'])}" target="_blank"><img loading="lazy" src="{esc(r['preview'])}" alt="{esc(r['title'])}"></a>
              <div class="body"><small>{esc(r['id'])}</small><h2>{esc(r['title'])}</h2>
              <p>{esc(r['note'])}</p><nav><a href="{esc(r['svg'])}" download>SVG 다운로드</a>
              <a href="{esc(r['data'])}" download>수치 CSV</a></nav><details><summary>출처</summary>{source_links}</details></div></article>''')
        options = "".join(f'<option value="{k}">{v}</option>' for k, v in CATEGORY_LABELS.items())
        page = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>전체 실험 SVG 갤러리</title><style>
        *{box-sizing:border-box}body{margin:0;background:#f4f6fa;color:#162234;font:15px/1.6 system-ui,sans-serif}
        header{max-width:1400px;margin:0 auto;padding:44px 28px 24px}h1{font-size:30px;margin:0 0 10px}header p{max-width:1000px}
        .controls{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0 12px}input,select{padding:12px;border:1px solid #bdc7d5;border-radius:7px;background:white;font:inherit}
        input{flex:1;min-width:200px}main{max-width:1400px;margin:auto;padding:0 28px 40px;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr));gap:24px}
        article{border:1px solid #dce2ea;border-radius:10px;background:white;overflow:hidden}article[hidden]{display:none}
        img{width:100%;height:340px;object-fit:contain;background:white;border-bottom:1px solid #e5e9ef}.body{padding:20px}small{color:#4e6078;font-family:monospace}
        h2{font-size:19px;line-height:1.4;margin:8px 0}p{color:#526078}nav{display:flex;gap:18px}a{color:#0069a6}details{margin-top:14px;font-size:12px;overflow-wrap:anywhere}
        </style><header><h1>전체 실험 · SVG 갤러리</h1><p>matplotlib으로 만든 __COUNT__개 그림입니다. 이미지를 누르면 확대 가능한 SVG가 열립니다.
        수정할 그림의 ID를 알려주세요. 각 그림에 수치 CSV와 출처를 연결했습니다.</p>
        <p>원본 로그가 없는 실험은 보고서 표에서 옮겼습니다. LLM 성능, 오프라인 선택, scripted gate, smoke를 구분해 읽어주세요.</p>
        <a href="README.md">설명·재생성 방법</a> · <a href="manifest.json">출처 목록</a>
        <div class="controls"><input id="q" placeholder="그림 ID, 주제, 지표 검색"><select id="category"><option value="">전체 실험</option>__OPTIONS__</select></div><span id="count"></span></header>
        <main>__CARDS__</main><script>
        const q=document.querySelector('#q'),cat=document.querySelector('#category'),cards=[...document.querySelectorAll('article')];
        function filter(){let n=0;for(const card of cards){const show=(!cat.value||cat.value===card.dataset.category)&&card.dataset.search.toLowerCase().includes(q.value.toLowerCase());card.hidden=!show;n+=show;}document.querySelector('#count').textContent=n+'개 그림';}
        q.addEventListener('input',filter);cat.addEventListener('change',filter);filter();</script></html>'''
        page = page.replace("__COUNT__", str(len(self.records))).replace("__OPTIONS__", options).replace("__CARDS__", "\n".join(cards))
        (self.output / "index.html").write_text(page, encoding="utf-8")
