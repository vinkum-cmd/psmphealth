"""Render a PSMP session transcript (text) as a terminal-style PNG. Pure drawing: input is text that is already redacted by the engine
(the vault password never appears in it), output is a PNG file. Nothing is executed."""
import re, textwrap
from PIL import Image, ImageDraw, ImageFont

FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"]
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")

def transcript_from_log(text):
    """Keep the engine log lines of one attempt: our notes, plus the ssh output with its 'ssh: ' prefix removed."""
    out = []
    for ln in ANSI.sub("", text).replace("\r", "").splitlines():
        out.append(ln[len("    ssh: "):] if ln.startswith("    ssh: ") else ln)
    return out

def render(lines, path, title, verdict, font_size=14, max_lines=40, width_chars=110):
    font = None
    for f in FONTS:
        try: font = ImageFont.truetype(f, font_size); break
        except OSError: continue
    font = font or ImageFont.load_default()
    lines = [w for ln in lines for w in (textwrap.wrap(ln.expandtabs(4), width_chars, subsequent_indent="    ", drop_whitespace=False) or [""])] or ["(no output)"]
    if len(lines) > max_lines: lines = lines[:max_lines // 2] + ["... (%d lines omitted; see the detail log) ..." % (len(lines) - max_lines)] + lines[-(max_lines // 2):]
    bb = font.getbbox("M"); cw = font.getlength("M"); lh = (bb[3] - bb[1]) + 6
    W = int(cw * width_chars) + 24; bar = 30; H = bar + 12 + lh * len(lines) + 12
    ok = verdict == "PASS"
    im = Image.new("RGB", (W, H), (24, 24, 24)); d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, bar], fill=(46, 125, 50) if ok else (179, 38, 30))
    d.text((10, 6), f"{title}  -  {verdict}", fill=(255, 255, 255), font=font)
    y = bar + 10
    for ln in lines:
        col = (230, 230, 230)
        if re.search(r"FAIL|denied|refused|timed out|error", ln, re.I): col = (255, 140, 130)
        elif re.search(r"\bOK\b|^\[[12]\] ", ln): col = (140, 230, 150)
        d.text((12, y), ln, fill=col, font=font); y += lh
    im.save(path, "PNG", optimize=True)
    return path
