"""Schema growth over the simulated year, as an SVG (two small multiples: tables, rows).

  .venv/bin/python -m lab.bench.year_chart      -> lab/results/year_growth.svg
"""
from __future__ import annotations

import json

from lab.bench.year import run_dir
from lab.common.llm import LAB_DIR

# (run, label, color): the first three slots of the validated categorical palette (all pairs pass CVD checks)
SERIES = [(("low", "v22", "gpt-6-luna"), "v2.2 Luna", "#2a78d6"),
          (("low", "v23", "gpt-6-luna"), "v2.3 Luna", "#eb6834"),
          (("low", "v23", "gpt-6-sol"), "v2.3 Sol", "#1baf7a")]
W, H, PAD_L, PAD_R, PAD_T, PAD_B = 420, 210, 44, 124, 34, 30


def panel(x0: int, title: str, key: str, data: dict) -> str:
    xs_max = max(g["messages"] for s in data.values() for g in s)
    ys_max = max(g[key] for s in data.values() for g in s) * 1.1
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    def px(v):
        return x0 + PAD_L + pw * v / xs_max

    def py(v):
        return PAD_T + ph * (1 - v / ys_max)

    out = [f'<text x="{x0 + PAD_L}" y="18" class="t">{title}</text>']
    for frac in (0, 0.5, 1):
        v = ys_max * frac
        out.append(f'<line x1="{x0 + PAD_L}" x2="{x0 + PAD_L + pw}" y1="{py(v):.1f}" y2="{py(v):.1f}" class="grid"/>'
                   f'<text x="{x0 + PAD_L - 6}" y="{py(v) + 4:.1f}" class="ax" text-anchor="end">{v:.0f}</text>')
    for v in (0, xs_max // 2, xs_max):
        out.append(f'<text x="{px(v):.1f}" y="{H - 10}" class="ax" text-anchor="middle">{v}</text>')
    out.append(f'<text x="{x0 + PAD_L + pw / 2}" y="{H}" class="ax" text-anchor="middle">messages ingested</text>')
    ends = []
    for run, label, color in SERIES:
        pts = data.get(run) or []
        if not pts:
            continue
        path = " ".join(f"{'M' if i == 0 else 'L'}{px(g['messages']):.1f},{py(g[key]):.1f}" for i, g in enumerate(pts))
        out.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2"/>')
        for g in pts:
            out.append(f'<circle cx="{px(g["messages"]):.1f}" cy="{py(g[key]):.1f}" r="6" fill="transparent">'
                       f'<title>{label}: {g[key]} {key} after {g["messages"]} messages ({g["ts"][:10]})</title></circle>')
        ends.append([py(pts[-1][key]) + 4, px(pts[-1]["messages"]) + 6, f"{pts[-1][key]} · {label}"])
    ends.sort()
    for i in range(1, len(ends)):  # keep end labels at least 13px apart
        ends[i][0] = max(ends[i][0], ends[i - 1][0] + 13)
    out += [f'<text x="{x:.1f}" y="{y:.1f}" class="lbl">{t}</text>' for y, x, t in ends]
    return "".join(out)


def main():
    data = {}
    for run, _, _ in SERIES:
        f = run_dir(*run) / "ingest.json"
        if f.exists():
            data[run] = json.loads(f.read_text())["growth"]
    legend = "".join(f'<rect x="{12 + i * 170}" y="{H + 18}" width="12" height="3" fill="{c}"/>'
                     f'<text x="{30 + i * 170}" y="{H + 23}" class="ax">{label} (low reasoning)</text>' for i, (_, label, c) in enumerate(SERIES))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {2 * W} {H + 32}" width="{2 * W}" height="{H + 32}" '
           f'font-family="-apple-system, Inter, sans-serif"><style>.t{{font-size:13px;fill:#0b0b0b;font-weight:600}}'
           f'.ax{{font-size:11px;fill:#52514e}}.lbl{{font-size:11px;fill:#0b0b0b}}.grid{{stroke:#e6e4de;stroke-width:1}}</style>'
           f'<rect width="100%" height="100%" fill="#fcfcfb"/>'
           f'{panel(0, "Tables in the schema", "tables", data)}{panel(W, "Rows stored", "rows", data)}{legend}</svg>')
    out = LAB_DIR / "results" / "year_growth.svg"
    out.write_text(svg)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
