"""Export the latest archived memory ablation as seven editable SVG figures.

The measured experiment is dated 2026-11-11. The 2026-11-17 topic-balanced
generator and safety-gate recovery runs are different experiments. This script
reads existing aggregates/reports only; it never calls a model or reruns tasks.

Run: .venv/Scripts/python.exe experiments/plot_ablation_results.py
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import zipfile

import matplotlib.pyplot as plt
import numpy as np

from visualization.common import COLORS, PlotContext, configure, vector_heatmap

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = "docs/results/ablation_2k_h100_20261111/summary.json"
DELTAS = "docs/results/ablation_2k_h100_20261111/deltas.json"
VALIDATION = "docs/results/ablation_2k_h100_20261111/validation.json"
REPORT = "docs/experiments/reports/ablation-2k-h100-2026-11-11.md"
COMPARISON = "docs/experiments/reports/ablation-2k-h100-analysis-2026-11-11.md"
TEMPORAL = "docs/experiments/reports/no-temporal-v104-2k-h100-2026-11-11.md"
KEYS = ["proposed", "proposed/no_constraint_preservation", "proposed/no_temporal",
        "proposed/no_state_mandatory", "proposed/no_budget_aware_packing"]
LABELS = ["Full Proposed", "No constraint\npreservation", "No temporal\n+ no mandatory state",
          "No mandatory\nstate", "No token-aware\npacking"]
SHORT = ["Full", "No constraints", "No temporal + mandatory", "No mandatory", "No packing"]
DISPLAY = {
    "ablation_01_task_success": ("작업 성공률", "2K와 4K에서 Full과 네 제거 조건을 비교합니다. 두 예산의 400개 대응 실행은 같은 결과였으며, v1.0.3의 No temporal은 필수 상태 보존도 함께 제거했습니다."),
    "ablation_02_memory_and_behavior": ("기억 보존과 행동 신뢰성", "보호 기억 회수, 시간 문맥 정확성, 실제 상태 사용, 오래된 상태 사용을 나눠 보여줍니다. 성공률이 높아도 보호 기억 보존이 낮아질 수 있습니다."),
    "ablation_03_paired_success_difference": ("Full 대비 성공률 차이", "같은 과제와 반복을 맞춘 Full − 제거 조건의 차이입니다. 저장된 짝지은 신뢰구간이 모두 0에 닿거나 포함하므로 확정적 우위나 동등성으로 해석하지 않습니다."),
    "ablation_04_token_usage": ("입력과 출력 토큰", "왼쪽 입력은 실행별 최대 단일 Context의 평균이고, 오른쪽 출력은 실행 전체 누적 출력의 평균입니다. 입력 곡선을 여러 호출의 누적 입력량으로 읽으면 안 됩니다."),
    "ablation_05_cost_and_latency": ("추정 비용과 지연 시간", "실행당 생성 비용과 두 실행 환경에서의 지연 시간을 비교합니다. 2K와 4K의 OS·commit이 달라 지연 차이를 예산 효과로 해석하지 않습니다."),
    "ablation_06_retrieval_and_failures": ("검색 성능과 실패 진단", "초기 검색 점수와 선택·상태·제약 실패를 함께 비교합니다. 실패 지표는 서로 겹칠 수 있으며, 적용 분모는 CSV에 기록했습니다."),
    "ablation_07_temporal_only_rerun": ("Temporal 단독 제거 재실행", "필수 상태 보존을 복구한 v1.0.4의 Temporal 단독 제거 결과를 기존 조건과 구분합니다. 수정 전후 80쌍의 성공 판정과 최종 선택 기억 집합은 같았지만, 일반적인 기능 불필요성을 입증한 결과는 아닙니다."),
}
BASE_NOTE = ("Experiment: 11 Nov 2026; Gemini 3.1 Flash-Lite; horizon=100 turns. "
             "40 base tasks x 2 repeats per condition; repeats are not independent tasks. "
             "Legacy v1.0.3 no_temporal removes BOTH temporal resolution and mandatory state.")


def metric_records(rows, key, factor=1):
    records = []
    for row in rows:
        m = row["metrics"][key]
        ci = m["ci95"]
        records.append({"strategy": row["strategy"], "metric": key,
                        "mean": m["mean"], "ci95_low": ci[0] if ci else None,
                        "ci95_high": ci[1] if ci else None, "n": m["n"],
                        "n_samples": m.get("n_samples"), "display_factor": factor,
                        "budget": 2048, "freeze": "1.0.3", "origin": SUMMARY})
    return records


def horizontal(ctx, ax, means, labels, ylabel, ci=None, counts=None, xmax=None):
    y = np.arange(len(means))
    kwargs = {}
    if ci is not None:
        kwargs["xerr"] = np.array([[max(0, v - c[0]) for v, c in zip(means, ci)],
                                   [max(0, c[1] - v) for v, c in zip(means, ci)]])
        kwargs["capsize"] = 3
        kwargs["error_kw"] = {"elinewidth": .9}
    bars = ax.barh(y, means, height=.60, color=COLORS[:len(means)], **kwargs)
    ax.set_yticks(y, labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel(ylabel)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.grid(axis="x", alpha=.18)
    ax.set_axisbelow(True)
    right = xmax if xmax is not None else max([c[1] for c in ci] if ci else means) * 1.24
    ax.set_xlim(0, right)
    for i, (bar, value) in enumerate(zip(bars, means)):
        end = ci[i][1] if ci is not None else value
        text = f"{value:.1f}" if counts is None else counts[i]
        ax.text(end + right*.018, bar.get_y()+bar.get_height()/2, text,
                va="center", fontsize=9)


def save(ctx, fig, chart_id, title, sources, note, data):
    ctx.save(fig, chart_id, title, "ablation", sources, note, data)


def success(ctx, rows):
    metric = [r["metrics"]["task_success"] for r in rows]
    # The report supplies 4K values and intervals, separately from the 2K JSON.
    report_4k = {"mean_pct": [97.5, 100, 90, 97.5, 97.5],
                 "ci_pct": [[92.5,100], [100,100], [80,97.5], [92.5,100], [92.5,100]]}
    assert np.allclose([m["mean"]*100 for m in metric], report_4k["mean_pct"])
    fig, axes = plt.subplots(1, 2, figsize=(13.6, 5.6))
    data = metric_records(rows, "task_success", 100)
    for ax, budget in zip(axes, [2048,4096]):
        if budget == 2048:
            means = [m["mean"]*100 for m in metric]
            ci = [[x*100 for x in m["ci95"]] for m in metric]
        else:
            means, ci = report_4k["mean_pct"], report_4k["ci_pct"]
            data += [dict(strategy=s, metric="task_success", mean=v/100,
                          ci95_low=c[0]/100, ci95_high=c[1]/100, n=80, n_samples=40,
                          display_factor=100, budget=4096, freeze="1.0.3", origin=COMPARISON)
                     for s,v,c in zip(KEYS, means, ci)]
        horizontal(ctx, ax, means, LABELS, "Task success (%)", ci,
                   [f"{round(v*.8)}/80 ({v:g}%)" for v in means], xmax=126)
        ax.set_xticks([0,25,50,75,100])
        ax.set_title(f"{budget//1024}K budget | 400 stored runs")
    save(ctx, fig, "ablation_01_task_success", "Ablation: task success at 2K and 4K", [SUMMARY, REPORT, COMPARISON],
         BASE_NOTE + " Error bars: reported sample-cluster bootstrap 95% CI. Matched 2K/4K outcomes are identical; do not pool as independent tasks. "
         "A zero-width empirical CI does not imply zero population uncertainty.", data)


def quality(ctx, rows):
    specs = [("applicable_protected_recall", "Applicable protected recall", 40),
             ("temporal_context_correct", "Temporal context correctness", 80),
             ("required_state_accuracy", "Required-state action accuracy", 80),
             ("stale_state_usage_rate", "Stale-state usage (lower is better)", 60)]
    fig, axes = plt.subplots(2, 2, figsize=(14, 9.6))
    data = []
    for ax, (key,title,n) in zip(axes.flat, specs):
        values = [r["metrics"][key] for r in rows]
        horizontal(ctx, ax, [m["mean"]*100 for m in values], LABELS, "Rate (%)",
                   [[x*100 for x in m["ci95"]] for m in values],
                   [f"{m['mean']*100:.1f}%" for m in values], xmax=118)
        ax.set_title(f"{title}\nApplicable denominator = {n}")
        ax.set_xticks([0,25,50,75,100])
        data += metric_records(rows, key, 100)
    save(ctx, fig, "ablation_02_memory_and_behavior", "Ablation: memory preservation and behavior reliability", [SUMMARY, REPORT, COMPARISON],
         BASE_NOTE + " 2K means and saved 95% CI; 4K means match. Context correctness and action correctness are distinct. "
         "All action-level constraint violations are 0/40; this does not guarantee perfect memory retention.", data)


def paired(ctx, deltas):
    fig, ax = plt.subplots(figsize=(11.4, 5))
    data = []
    for i, d in enumerate(deltas):
        m = d["delta_success"]
        value, low, high = m["mean"]*100, m["ci95"][0]*100, m["ci95"][1]*100
        ax.errorbar(value, i, xerr=[[max(0,value-low)],[max(0,high-value)]],
                    fmt="o", color=COLORS[i+1], capsize=4, markersize=7)
        ax.text(1.03, i, f"{value:+.1f} pp  [{low:+.1f}, {high:+.1f}]", va="center",
                transform=ax.get_yaxis_transform(), fontsize=9)
        data.append(dict(baseline=d["baseline"], delta_pp=value, ci95_low_pp=low, ci95_high_pp=high,
                         n_pairs=d["n_pairs"], n_samples=m["n_samples"], budget=2048,
                         freeze="1.0.3", origin=DELTAS))
    ax.set_yticks(range(4), LABELS[1:])
    ax.set_ylim(3.65, -.65)
    ax.axvline(0, color="#555555", linewidth=1, linestyle="--")
    ax.set_xlim(-11,24)
    ax.set_xticks([-10,-5,0,5,10,15,20])
    ax.set_xlabel("Full minus removed-component task success (percentage points)")
    ctx.style(ax)
    save(ctx, fig, "ablation_03_paired_success_difference", "Ablation: paired success differences and 95% CI", [DELTAS, REPORT, COMPARISON],
         BASE_NOTE + " 2K: 80 sample/repeat pairs, clustered by 40 base tasks; 2,000 bootstrap resamples, seed 42. "
         "All intervals touch or include zero; no multiplicity adjustment or equivalence test.", data)


def tokens(ctx, rows):
    fig, axes = plt.subplots(1,2,figsize=(13.4,5.5))
    data = []
    specs = [("input_tokens", "Peak counted context per run"),
             ("output_tokens", "Cumulative output per run")]
    for ax, (key,title) in zip(axes,specs):
        values = [r["metrics"][key] for r in rows]
        horizontal(ctx, ax, [m["mean"] for m in values], LABELS, "Tokens",
                   [m["ci95"] for m in values])
        ax.set_title(title)
        data += metric_records(rows,key)
    save(ctx,fig,"ablation_04_token_usage","Ablation: peak input and cumulative output tokens", [SUMMARY, COMPARISON],
         BASE_NOTE + " Saved 2K means and 95% CI; 4K means match. Input is a per-run maximum single context, not cumulative provider prompt usage. "
         "Output is cumulative across all calls in a run.",data)


def resources(ctx, rows):
    fig, axes = plt.subplots(1,2,figsize=(14,5.8))
    costs=[r["metrics"]["total_llm_cost"] for r in rows]
    horizontal(ctx, axes[0], [m["mean"]*1000 for m in costs], LABELS,
               "Estimated generation USD / 1,000 stored runs",
               [[x*1000 for x in m["ci95"]] for m in costs],
               counts=[f"{m['mean']*1000:.3f}" for m in costs])
    axes[0].set_title("Generation cost (2K; 4K means match)")
    # Different machines/commits: show measurements without causal budget claims.
    latency_2k=[r["metrics"]["planning_latency_ms"]["mean"]/1000 for r in rows]
    latency_4k=[4.2601,4.3377,4.8106,4.2407,4.2834]
    y=np.arange(5)
    axes[1].barh(y-.16,latency_2k,.30,label="2K / Darwin arm64",color=COLORS[0])
    axes[1].barh(y+.16,latency_4k,.30,label="4K / Windows AMD64",color=COLORS[1])
    axes[1].set_yticks(y,LABELS,fontsize=9)
    axes[1].invert_yaxis()
    axes[1].set_xlabel("Mean planning latency (seconds / run)")
    axes[1].set_xlim(0,6.3)
    axes[1].set_title("Descriptive latency across different environments")
    axes[1].spines[["top","right","left"]].set_visible(False)
    axes[1].grid(axis="x",alpha=.18)
    axes[1].set_axisbelow(True)
    axes[1].legend(loc="lower center",bbox_to_anchor=(.5,1.10),ncol=2,frameon=False,fontsize=8)
    for i in range(5):
        axes[1].text(latency_2k[i]+.08,y[i]-.16,f"{latency_2k[i]:.2f}",va="center",fontsize=8)
        axes[1].text(latency_4k[i]+.08,y[i]+.16,f"{latency_4k[i]:.2f}",va="center",fontsize=8)
    data=metric_records(rows,"total_llm_cost",1000)+metric_records(rows,"planning_latency_ms",.001)
    data += [dict(strategy=s,metric="planning_latency_ms",mean=v*1000,n=80,n_samples=40,
                  budget=4096,freeze="1.0.3",origin=COMPARISON,display_factor=.001)
             for s,v in zip(KEYS,latency_4k)]
    save(ctx,fig,"ablation_05_cost_and_latency","Ablation: estimated generation cost and recorded latency",[SUMMARY,COMPARISON],
         BASE_NOTE + " Cost bars use saved 95% CI scaled to 1,000 runs; this is a rate, not an actual 1,000-run experiment or total bill. "
         "Latency bars have no invented CI; OS and commit differ, so differences are not a causal budget effect.",data)


def diagnostics(ctx,rows):
    specs=[(["recall_at_k","precision_at_k","mrr"], ["Recall@k","Precision@k","MRR"],"Retrieval score x 100","Blues"),
           (["general_retrieval_miss","selection_failure","budget_selection_failure",
             "state_omission_rate","state_error_rate","constraint_violation_rate"],
            ["Retrieval\nmiss","Selection\nfailure","Budget\nselection","State\nomission","State\nerror","Constraint\nviolation"],
            "Failure rate (%)","Oranges")]
    fig,axes=plt.subplots(1,2,figsize=(15.5,5.7),gridspec_kw={"width_ratios":[1,1.7]})
    data=[]
    for column,(ax,(metrics,names,title,cmap)) in enumerate(zip(axes,specs)):
        values=np.array([[r["metrics"][k]["mean"]*100 for k in metrics] for r in rows])
        vector_heatmap(ax,values,0,100,cmap)
        ax.set_xticks(range(len(names)),names,fontsize=9)
        ax.set_yticks(range(5),LABELS if column==0 else [""]*5,fontsize=9)
        ax.set_title(title)
        ax.tick_params(axis="both",length=0,pad=7)
        for (j,i),value in np.ndenumerate(values):
            ax.text(i,j,f"{value:.1f}",ha="center",va="center",fontsize=10,
                    color="white" if value>55 else "#222222")
        for key in metrics:
            data += metric_records(rows,key,100)
    save(ctx,fig,"ablation_06_retrieval_and_failures","Ablation: retrieval and overlapping failure diagnostics",[SUMMARY,REPORT,COMPARISON],
         BASE_NOTE + " 2K means; 4K means match. Recall/precision/MRR are multiplied by 100. "
         "Constraint violations use n=40; remaining shown diagnostics use n=80. Failure metrics overlap and must not be summed. Cells are vector shapes.",data)


def temporal(ctx,rows):
    labels=["Full v1.0.3\nTemporal ON\nMandatory ON", "Legacy v1.0.3\nTemporal OFF\nMandatory OFF",
            "Corrected v1.0.4\nTemporal OFF\nMandatory ON"]
    mfull=rows[0]["metrics"]["task_success"]
    mlegacy=rows[2]["metrics"]["task_success"]
    success_values=[mfull["mean"]*100,mlegacy["mean"]*100,90]
    success_ci=[[v*100 for v in mfull["ci95"]],[v*100 for v in mlegacy["ci95"]],[80,97.5]]
    stale=[0,100*8/60,100*8/60]
    fig,axes=plt.subplots(1,2,figsize=(13.2,5.4))
    x=np.arange(3)
    for ax,values,title,ylabel,ci,ylim in [
        (axes[0],success_values,"Task success, n=80 per condition","Task success (%)",success_ci,(0,117)),
        (axes[1],stale,"Current-state stale usage, n=60","Stale-state usage (%)",None,(0,20))]:
        kwargs={}
        if ci:
            kwargs={"yerr":[[max(0,v-c[0]) for v,c in zip(values,ci)],
                            [max(0,c[1]-v) for v,c in zip(values,ci)]],"capsize":4}
        bars=ax.bar(x,values,width=.6,color=[COLORS[0],COLORS[2],COLORS[3]],**kwargs)
        for i,(bar,v) in enumerate(zip(bars,values)):
            top=ci[i][1] if ci else v
            count=f"{[78,72,72][i]}/80" if ci else f"{[0,8,8][i]}/60"
            ax.text(bar.get_x()+bar.get_width()/2,top+(.018*(ylim[1]-ylim[0])),count,
                    ha="center",va="bottom",fontsize=10)
        ax.set_xticks(x,labels,fontsize=9)
        ax.set_ylim(*ylim)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ctx.style(ax)
    data=[dict(condition=label.replace("\n","; "),freeze=version,temporal_resolution=t,mandatory_state=m,
               task_success_pct=v,success_n=80,success_ci95_low=c[0],success_ci95_high=c[1],
               stale_usage_pct=s,stale_count=k,stale_n=60,origin=source)
          for label,version,t,m,v,c,s,k,source in zip(labels,["1.0.3","1.0.3","1.0.4"],
              [True,False,False],[True,False,True],success_values,success_ci,stale,[0,8,8],[SUMMARY,SUMMARY,TEMPORAL])]
    save(ctx,fig,"ablation_07_temporal_only_rerun","Temporal ablation: corrected mandatory-state-preserving rerun",[SUMMARY,COMPARISON,TEMPORAL],
         "Experiment: 11 Nov 2026; 2K/H100. Success bars: saved/reported cluster-bootstrap 95% CI; no stale-use CI was reported for corrected rerun. "
         "80/80 legacy/corrected outcome pairs and selected ID sets match. Corrected vs historical Full: +7.5 pp, 95% CI [-2.5,+17.5]; not a concurrent rerun of all variants.",data)


def write_package(ctx,validation):
    sources=sorted({p for r in ctx.records for p in r["sources"]}|{VALIDATION})
    manifest={"generated_on":"2026-11-17","experiment_date":"2026-11-11", "engine":"matplotlib",
              "scope":"Archived memory component ablation, 2K/4K H100; independent temporal-only rerun separated",
              "source_commit":validation["git_commit"],"chart_count":len(ctx.records),
              "charts":[dict(r,title_ko=DISPLAY[r["id"]][0],description_ko=DISPLAY[r["id"]][1]) for r in ctx.records],
              "sources":[dict(path=p,sha256=hashlib.sha256((ROOT/p).read_bytes()).hexdigest()) for p in sources],
              "limitations":["Current freeze 1.0.6 and topic_balanced_v3 config are not the experiment configuration.",
                 "11 Nov ablation results are separate from 17 Nov safety-gate recovery and input-generation QA.",
                 "The v1.0.3 temporal ablation removes two components; v1.0.4 temporal-only rerun is separate.",
                 "4K values come from the published comparison report; 2K values from canonical checked-in aggregates.",
                 "Existing confidence intervals are preserved. No raw runs or new confidence intervals were reconstructed.",
                 "No experiments or model calls were made. 40 shared base tasks, not 800 independent tasks."]}
    (ctx.output/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    readme=["# Ablation SVG 시각화", "", "**생성일 2026-11-17 · 실험일 2026-11-11 · SVG 7개**", "",
            "[전체 미리보기](index.html) · [SVG 및 재생성 코드 ZIP](ablation_svg_20261117.zip)", "",
            "현재 저장소와 GitHub에서 확인되는 최신 구성요소 제거 성능 결과를 따로 정리했습니다. "
            "11월 17일의 topic_balanced_v3 생성 점검 및 Safety Gate 오류 복구는 이 ablation과 다른 실험입니다.", "",
            "## 결과와 해석", "",
            "- 2K와 4K의 다섯 조건은 각각 40개 기본 과제 × 2회 반복입니다. 두 예산의 400개 대응 실행은 같은 결과였으며 독립 과제 수가 늘어난 것은 아닙니다.",
            "- Full 성공률은 97.5%, 제약 보존 제거는 100%, 시간 해석과 필수 상태 동시 제거는 90%, 나머지 두 조건은 97.5%입니다. 짝지은 차이의 CI는 모두 0에 닿거나 포함합니다.",
            "- 제약 보존 제거의 성공률 100%와 보호 기억 회수율 90%를 함께 봐야 합니다. 기능 제거가 전반적으로 더 낫다는 결론은 지지하지 않습니다.",
            "- v1.0.3의 No temporal은 시간 해석과 필수 상태 보존을 함께 끕니다. v1.0.4의 Temporal 단독 제거 재실행은 7번 그림으로 구분했습니다.",
            "- 2K의 JSON 집계를 직접 읽고, 4K와 추가 재실행의 수치는 보고서 표에서 가져왔습니다. 없는 원본 로그나 신뢰구간을 만들지 않았습니다.", "",
            "## 그림별 안내", "", "| 파일 | 설명 |", "|---|---|"]
    for r in manifest["charts"]:
        readme.append(f"| [{r['id']}.svg]({r['svg']}) | **{r['title_ko']}** — {r['description_ko']} |")
    readme += ["", "## 파일 구성과 재생성", "",
               "`svg/`는 글자와 도형을 편집할 수 있는 SVG, `previews/`는 PNG 미리보기, `data/`는 그림에 사용한 수치 CSV입니다. 출처와 SHA-256은 `manifest.json`에 있습니다.", "",
               "저장소 루트에서 다음 명령으로 다시 생성합니다.", "", "```powershell",
               ".\\.venv\\Scripts\\python.exe experiments/plot_ablation_results.py", "```", "",
               "ZIP의 `reproduce/`에는 코드·사용한 집계·보고서가 같은 상대 경로로 들어 있습니다. "
               "필요한 패키지는 `reproduce/experiments/visualization/requirements.txt`에 명시되어 있습니다.", ""]
    (ctx.output/"README.md").write_text("\n".join(readme),encoding="utf-8")
    cards=[]
    for r in manifest["charts"]:
        e=html.escape
        cards.append(f'''<article><a href="{r['svg']}" target="_blank"><img src="{r['preview']}" alt="{e(r['title_ko'])}"></a>
<div><small>{r['id']}.svg</small><h2>{e(r['title_ko'])}</h2><p>{e(r['description_ko'])}</p>
<a href="{r['svg']}" download>SVG 다운로드</a> · <a href="{r['data']}" download>수치 CSV</a></div></article>''')
    page='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ablation SVG 시각화</title><style>*{box-sizing:border-box}body{margin:0;background:#f4f6fa;color:#172234;font:15px/1.65 system-ui,sans-serif}
header{max-width:1360px;padding:36px 24px 24px;margin:auto}h1{margin:0 0 10px;font-size:28px}header p{max-width:1050px}
main{max-width:1360px;margin:auto;padding:0 24px 40px;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,500px),1fr));gap:24px}
article{background:white;border:1px solid #d9e0e8;border-radius:10px;overflow:hidden}article div{padding:20px}img{display:block;width:100%;height:330px;object-fit:contain;border-bottom:1px solid #e1e6ee}
h2{font-size:20px;margin:7px 0}small{font:12px monospace;color:#536076}a{color:#006ba4}p{color:#4b586b}</style>
<header><h1>Ablation 실험 SVG 7개</h1><p>실험일 2026년 11월 11일 · 생성일 2026년 11월 17일<br>
2K·4K 구성요소 제거와 Temporal 단독 제거 재실행입니다. 새 topic_balanced_v3 성능 실험이나 Safety Gate 복구 결과와 구분했습니다.</p>
<a href="ablation_svg_20261117.zip">전체 ZIP 다운로드</a> · <a href="README.md">출처와 해석</a></header><main>__CARDS__</main></html>'''
    (ctx.output/"index.html").write_text(page.replace("__CARDS__","\n".join(cards)),encoding="utf-8")
    bundle=ctx.output/"ablation_svg_20261117.zip"
    code=["experiments/plot_ablation_results.py","experiments/visualization/__init__.py",
          "experiments/visualization/common.py","experiments/visualization/requirements.txt"]
    with zipfile.ZipFile(bundle,"w",zipfile.ZIP_DEFLATED) as z:
        files=[ctx.output/p for p in ["README.md","manifest.json","index.html"]]
        files += [ctx.output/r[k] for r in ctx.records for k in ["svg","data","preview"]]
        for p in files:
            z.write(p,p.relative_to(ctx.output).as_posix())
        for path in sorted(set(sources+code)):
            z.write(ROOT/path,"reproduce/"+path)
    print(f"Saved {len(ctx.records)} SVGs and bundle: {bundle}")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"docs/results/ablation_visualizations_20261117")
    args=parser.parse_args()
    configure()
    plt.rcParams["svg.hashsalt"]="hai-ablation-20261117"
    rows=json.loads((ROOT/SUMMARY).read_text(encoding="utf-8"))
    rows=sorted(rows,key=lambda r:KEYS.index(r["strategy"]))
    assert [r["strategy"] for r in rows]==KEYS
    assert all(r["n_runs"]==80 and r["n_samples"]==40 for r in rows)
    deltas=json.loads((ROOT/DELTAS).read_text(encoding="utf-8"))
    validation=json.loads((ROOT/VALIDATION).read_text(encoding="utf-8"))
    ctx=PlotContext(ROOT,args.output.resolve())
    success(ctx,rows)
    quality(ctx,rows)
    paired(ctx,deltas)
    tokens(ctx,rows)
    resources(ctx,rows)
    diagnostics(ctx,rows)
    temporal(ctx,rows)
    write_package(ctx,validation)


if __name__=="__main__":
    main()
