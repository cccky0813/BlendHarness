"""Render docs/diagrams/*.mmd (Mermaid) to PNG images for the README, with large fonts.

    python scripts/render_diagrams.py              # all diagrams -> docs/diagrams/*.png

Uses the locally installed Google Chrome in headless mode (no Node / mermaid-cli needed). Mermaid itself is
loaded from the jsDelivr CDN. Two passes per diagram: measure the rendered SVG, then screenshot exactly that
size at 2x, then trim the white margin with Pillow.
"""

import html
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
DIAGRAMS = ROOT / "docs" / "diagrams"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"
SCALE = 2

PAGE = """<!doctype html><meta charset="utf-8">
<style>body{{margin:0;background:#ffffff}} #out{{display:inline-block;padding:24px}}</style>
<div id="out"></div><div id="size" style="display:none"></div>
<script src="{mermaid}"></script>
<script>
mermaid.initialize({{
  startOnLoad: false, theme: "base",
  themeVariables: {{
    fontFamily: "PingFang SC, Helvetica Neue, Arial, sans-serif", fontSize: "20px",
    primaryColor: "#fff3e6", primaryBorderColor: "#d9700b", primaryTextColor: "#1c2430",
    lineColor: "#3a4452", signalColor: "#3a4452", signalTextColor: "#1c2430",
    noteBkgColor: "#eef1f5", noteBorderColor: "#b9c1cc", noteTextColor: "#1c2430",
    labelBoxBkgColor: "#fff3e6", labelBoxBorderColor: "#d9700b", labelTextColor: "#1c2430",
    loopTextColor: "#1c2430", sequenceNumberColor: "#ffffff", activationBkgColor: "#fde3c4"
  }},
  sequence: {{
    actorFontSize: 20, messageFontSize: 20, noteFontSize: 19, actorFontWeight: 600,
    width: 170, actorMargin: 30, boxMargin: 12, messageMargin: 44, noteMargin: 14,
    mirrorActors: true, wrap: true, useMaxWidth: false
  }}
}});
mermaid.render("d", {source}).then(r => {{
  document.getElementById("out").innerHTML = r.svg;
  const box = document.getElementById("out").getBoundingClientRect();
  document.getElementById("size").textContent = "SIZE=" + Math.ceil(box.width) + "x" + Math.ceil(box.height);
}}).catch(e => {{ document.getElementById("size").textContent = "ERROR=" + e; }});
</script>"""


def chrome(*args: str) -> str:
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--virtual-time-budget=20000", *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180).stdout


def render(mmd: Path, workdir: Path) -> Path:
    page = workdir / (mmd.stem + ".html")
    source = "`" + mmd.read_text(encoding="utf-8").replace("\\", "\\\\").replace("`", "\\`") + "`"
    page.write_text(PAGE.format(mermaid=MERMAID, source=source), encoding="utf-8")

    dom = chrome("--window-size=4000,3000", "--dump-dom", page.as_uri())          # pass 1: measure
    match = re.search(r"SIZE=(\d+)x(\d+)", dom)
    if not match:
        error = re.search(r"ERROR=([^<]*)", dom)
        raise RuntimeError(f"{mmd.name}: {html.unescape(error.group(1)) if error else 'Mermaid did not render'}")
    width, height = int(match.group(1)) + 40, int(match.group(2)) + 240   # generous: trimmed afterwards

    png = mmd.with_suffix(".png")                                                 # pass 2: screenshot
    chrome(f"--window-size={width},{height}", f"--force-device-scale-factor={SCALE}",
           f"--screenshot={png}", page.as_uri())
    image = Image.open(png).convert("RGB")
    bbox = ImageChops.difference(image, Image.new("RGB", image.size, (255, 255, 255))).getbbox()
    margin = 24 * SCALE
    if bbox:
        image = image.crop((max(0, bbox[0] - margin), max(0, bbox[1] - margin),
                            min(image.width, bbox[2] + margin), min(image.height, bbox[3] + margin)))
    image.save(png, optimize=True)
    return png


if __name__ == "__main__":
    names = sys.argv[1:]
    with tempfile.TemporaryDirectory() as tmp:
        for mmd in sorted(DIAGRAMS.glob("*.mmd")):
            if names and mmd.stem not in names:
                continue
            out = render(mmd, Path(tmp))
            w, h = Image.open(out).size
            print(f"{out.relative_to(ROOT)}  {w}x{h}px  {out.stat().st_size // 1024} KB")
