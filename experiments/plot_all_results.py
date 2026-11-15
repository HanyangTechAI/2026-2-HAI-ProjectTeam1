"""Generate every archived experiment SVG, CSV, preview, and a browseable index.

Run from the repository root: python experiments/plot_all_results.py
This only reads archived data/reports; it never runs an experiment or calls a model.
"""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path
import zipfile

from visualization.common import PlotContext, configure


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "docs/results/all_visualizations_20261115")
    parser.add_argument("--groups", nargs="+", default=["main", "preliminary", "followups", "criticality", "audits"],
                        choices=["main", "preliminary", "followups", "criticality", "audits"])
    args = parser.parse_args()
    output = args.output.resolve()
    configure()
    ctx = PlotContext(root, output)
    for group in args.groups:
        module = importlib.import_module(f"visualization.{group}")
        module.build(ctx)
    manifest = ctx.write_index()
    # Bundle figures together with the exact source documents and generation code.
    bundle = output / "all_experiment_svg_20261115.zip"
    code = [root / "experiments/plot_all_results.py"]
    code += sorted((root / "experiments/visualization").glob("*.py"))
    code += [root / "experiments/visualization/requirements.txt"]
    source = [root / item["path"] for item in manifest["sources"]]
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as z:
        artifacts = [output / name for name in ("README.md", "index.html", "manifest.json")]
        artifacts += [output / row[key] for row in ctx.records for key in ("svg", "preview", "data")]
        for path in sorted(artifacts):
            if path.name == "index.html":
                # Original repo gallery links outside its output folder; ZIP is standalone.
                page = path.read_text(encoding="utf-8").replace('href="../../../', 'href="reproduce/')
                z.writestr("index.html", page)
            else:
                z.write(path, path.relative_to(output).as_posix())
        for path in sorted(set(code + source)):
            z.write(path, "reproduce/" + path.relative_to(root).as_posix())
    missing = [x["source"] for x in manifest["coverage"] if not x["charts"]]
    print(f"\nSaved {len(ctx.records)} charts: {output}")
    print(f"Bundle: {bundle}")
    print(f"Report coverage: {len(manifest['coverage']) - len(missing)}/{len(manifest['coverage'])}")
    if missing:
        print("Reports without charts: " + ", ".join(missing))


if __name__ == "__main__":
    main()
