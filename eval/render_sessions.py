"""Render a session file (the answer key of the multi-turn eval) as a readable Markdown page.

    python eval/render_sessions.py                                   # eval/sessions_draft.json -> docs/sessions_draft.md
    python eval/render_sessions.py eval/sessions_x.json docs/sessions_x.md

The JSON stays the source of truth; re-run this after editing it so the two never drift apart.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "eval" / "sessions_draft.json"
dst = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "docs" / "sessions_draft.md"
HUE = {"red": "红", "yellow": "黄", "green": "绿", "blue": "蓝", "orange": "橙", "purple": "紫"}
DIR = {"east": "东", "west": "西", "north": "北", "south": "南"}


def pat(p: str) -> str:
    return "`" + p.replace("|", " \\| ") + "`"


def describe(c: dict) -> str:
    if "exists" in c:
        return f"存在名字匹配 {pat(c['exists'])} 的物体"
    if "absent" in c:
        return f"**不存在**名字匹配 {pat(c['absent'])} 的物体"
    if "count" in c:
        rng = f"至少 {c.get('min', 0)} 个" + (f"、至多 {c['max']} 个" if "max" in c else "")
        return f"{pat(c['count'])} 的**实例**{rng}"
    if "near" in c:
        return f"{pat(c['near'][0])} 与 {pat(c['near'][1])} 的间隙 ≤ {c.get('max_gap', 0.05)} m"
    if "on" in c:
        return f"{pat(c['on'][0])} 放在 {pat(c['on'][1])} 上面（底面贴着对方顶面）"
    if "above" in c:
        return f"{pat(c['above'][0])} 整体在 {pat(c['above'][1])} 的上方"
    if "direction" in c:
        a, b = c["direction"]
        rng = f"{c.get('min', 0)}–{c['max']} m" if "max" in c else f"≥ {c.get('min', 0)} m"
        return f"{pat(a)} 在 {pat(b)} 的{DIR[c['dir']]}边 {rng}"
    if "grounded" in c:
        return f"{pat(c['grounded'])} 着地（最低点在地面附近）"
    if "contains_xy" in c:
        return f"{pat(c['contains_xy'][0])} 的水平范围包住 {pat(c['contains_xy'][1])}"
    if "evenly_spaced" in c:
        return f"{pat(c['evenly_spaced'])} 等距排列（间距变化 ≤ {int(c.get('tolerance', 0.15) * 100)}%）"
    if "material_hue" in c:
        return f"{pat(c['material_hue'])} 的材质是{HUE.get(c['hue'], c['hue'])}色"
    return f"未知检查 {c}"


data = json.loads(src.read_text(encoding="utf-8"))
ids = {c["id"]: describe(c) for s in data["sessions"] for t in s["turns"] for c in t["expect"]}
lines = [f"# 多轮测评的标准答案（{data.get('version', '')}）", "",
         f"> 由 `eval/render_sessions.py` 从 `{src.relative_to(ROOT)}` 生成，**不要直接编辑这个文件**：改 JSON，再重新生成。",
         "> 这些内容**只有测评脚本和离线裁判能看到**，系统每一轮只收到「用户说」那一句。", "",
         "**怎么读**：", "",
         "- **程序检查**：这一轮结束后，程序在物体清单上自动判断的条目。物体名用宽松的模式匹配，`a \\| b` 表示名字里有 a 或 b 都算；",
         "- **只允许改动**：这一轮可以新增、修改、删除的物体；其余物体有任何变化，都算**回归**；",
         "- **之后一直要成立**：之后每一轮都会重新检查，用来算**需求保持率**；",
         "- **前置条件**：依赖前面哪些检查。前置没满足时这一轮照常跑，但单独统计；",
         "- **指代**：需求里的「那个……」应该指向哪些物体，用来测指代消解；",
         "- **裁判的问题**：给 LLM 裁判看渲染图回答的是 / 否问题。「依赖 Q1」表示 Q1 答「否」时这一题不再问；P 开头的是「之前的东西有没有被改坏」。", ""]
for s in data["sessions"]:
    lines += [f"## {s['id']}（{s.get('theme', '')}）", ""]
    for k, t in enumerate(s["turns"], 1):
        lines += [f"### 第 {k} 轮", "", f"**用户说**：{t['request']}", "", "**程序检查**：", ""]
        lines += [f"- `{c['id']}`：{describe(c)}" for c in t["expect"]]
        touches = t.get("touches", [])
        lines += ["", "**只允许改动**：" + ("任何物体（第一轮）" if "*" in touches else "、".join(pat(p) for p in touches))]
        if t.get("persist"):
            lines += ["", "**之后一直要成立**：" + "；".join(f"`{i}`" for i in t["persist"])]
        if t.get("requires"):
            lines += ["", "**前置条件**：" + "；".join(f"`{i}`（{ids.get(i, '?')}）" for i in t["requires"])]
        if t.get("refs"):
            lines += ["", "**指代**：" + "；".join(f"「{k2}」→ {pat(v)}" for k2, v in t["refs"].items())]
        if t.get("rubric"):
            lines += ["", "**裁判的问题**：", ""]
            lines += [f"- **{q['id']}** {q['q']}" + (f"（依赖 {q['dep']}）" if q.get("dep") else "") for q in t["rubric"]]
        lines.append("")
dst.write_text("\n".join(lines), encoding="utf-8")
print(f"wrote {dst.relative_to(ROOT)}: {len(data['sessions'])} sessions, {len(ids)} checks")
