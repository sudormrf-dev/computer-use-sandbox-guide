"""Screenshot annotation tool for computer-use agent debugging.

Overlays action history and confidence scores onto screenshots using
PIL/Pillow. Useful for replaying recordings and understanding why an
agent made each decision.

Usage::

    python examples/screenshot_annotator.py --log session.jsonl --frame 3
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_actions(log_path: Path) -> list[dict[str, Any]]:
    """Load action log entries from a JSONL file.

    Args:
        log_path: Path to the ``.jsonl`` session log.

    Returns:
        List of action dicts (excludes session start/end events).
    """
    actions = []
    with log_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("event") in {"session_start", "session_end"}:
                continue
            actions.append(entry)
    return actions


def annotate_screenshot(
    image_bytes: bytes,
    action: dict[str, Any],
    action_index: int,
    total_actions: int,
) -> bytes:
    """Overlay action metadata onto a screenshot.

    Adds a semi-transparent header band with:
    - Action number / total
    - Action type and parameters
    - Screenshot hash for deduplication

    Requires Pillow (``pip install Pillow``).

    Args:
        image_bytes: Raw PNG/JPEG screenshot bytes.
        action: Action dict from the JSONL log.
        action_index: 0-indexed position in the session.
        total_actions: Total number of actions in the session.

    Returns:
        Annotated PNG bytes.
    """
    try:
        import io

        from PIL import Image, ImageDraw, ImageFont  # type: ignore[import]
    except ImportError as e:
        msg = "Pillow is required for annotation: pip install Pillow"
        raise ImportError(msg) from e

    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    width, _height = img.size

    # Header band height
    band_h = 40
    overlay = Image.new("RGBA", (width, band_h), (0, 0, 0, 180))
    img.paste(overlay, (0, 0), overlay)

    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 14)
    except OSError:
        font = ImageFont.load_default()

    action_type = action.get("action_type", "unknown")
    params = action.get("params", {})
    param_str = ", ".join(f"{k}={v!r}" for k, v in list(params.items())[:2])
    screenshot_hash = action.get("screenshot_hash", "")[:8]

    label = f"[{action_index + 1}/{total_actions}] {action_type}({param_str})  hash={screenshot_hash}"
    draw.text((8, 12), label, fill=(255, 255, 255, 255), font=font)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def annotate_session(
    log_path: Path,
    screenshots_dir: Path,
    output_dir: Path,
) -> list[Path]:
    """Annotate all screenshots in a session with action metadata.

    Args:
        log_path: Path to the JSONL session log.
        screenshots_dir: Directory containing ``frame_N.png`` files.
        output_dir: Where to write annotated images.

    Returns:
        List of paths to annotated output images.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    actions = _load_actions(log_path)
    output_paths: list[Path] = []

    for i, action in enumerate(actions):
        frame_path = screenshots_dir / f"frame_{i:04d}.png"
        if not frame_path.exists():
            continue

        raw = frame_path.read_bytes()
        annotated = annotate_screenshot(raw, action, i, len(actions))

        out_path = output_dir / f"annotated_{i:04d}.png"
        out_path.write_bytes(annotated)
        output_paths.append(out_path)
        print(f"  Annotated frame {i+1}/{len(actions)}: {out_path.name}")

    return output_paths


def build_html_replay(
    log_path: Path,
    annotated_dir: Path,
    output_html: Path,
) -> None:
    """Build a single-file HTML replay viewer from annotated frames.

    Creates an HTML file with a slider to step through the session
    frame by frame.

    Args:
        log_path: Path to the JSONL session log.
        annotated_dir: Directory with ``annotated_NNNN.png`` files.
        output_html: Output path for the HTML viewer.
    """
    actions = _load_actions(log_path)
    frames = sorted(annotated_dir.glob("annotated_*.png"))

    if not frames:
        print("No annotated frames found — skipping HTML viewer")
        return

    import base64

    frame_data = []
    for frame in frames:
        b64 = base64.b64encode(frame.read_bytes()).decode()
        frame_data.append(f"data:image/png;base64,{b64}")

    action_labels = []
    for a in actions:
        t = a.get("action_type", "?")
        p = a.get("params", {})
        label = f"{t}({', '.join(f'{k}={v!r}' for k, v in list(p.items())[:1])})"
        action_labels.append(label.replace("'", "\\'"))

    frames_js = json.dumps(frame_data)
    labels_js = json.dumps(action_labels)

    html = f"""<!DOCTYPE html>
<html>
<head><title>Agent Session Replay</title>
<style>
  body {{ font-family: monospace; background: #1a1a2e; color: #eee; margin: 20px; }}
  img {{ max-width: 100%; border: 1px solid #444; }}
  #label {{ margin: 8px 0; font-size: 14px; color: #7ec8e3; }}
  input[type=range] {{ width: 100%; margin: 8px 0; }}
</style>
</head>
<body>
<h2>Agent Session Replay</h2>
<p>Log: {log_path.name} — {len(frames)} frames</p>
<input type="range" id="slider" min="0" max="{len(frames)-1}" value="0" oninput="seek(this.value)">
<div id="label"></div>
<img id="frame">
<script>
const frames = {frames_js};
const labels = {labels_js};
function seek(n) {{
  document.getElementById('frame').src = frames[n];
  document.getElementById('label').textContent = '[' + (+n+1) + '/' + frames.length + '] ' + (labels[n] || '');
}}
seek(0);
</script>
</body>
</html>"""

    output_html.write_text(html, encoding="utf-8")
    print(f"HTML replay written to: {output_html}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Annotate computer-use agent screenshots")
    parser.add_argument("--log", required=True, help="Path to JSONL session log")
    parser.add_argument("--screenshots", required=True, help="Directory of frame PNGs")
    parser.add_argument("--output", default="annotated_frames", help="Output directory")
    parser.add_argument("--html", action="store_true", help="Also build HTML replay viewer")
    args = parser.parse_args()

    log_path = Path(args.log)
    screenshots_dir = Path(args.screenshots)
    output_dir = Path(args.output)

    print(f"Annotating session: {log_path}")
    annotated = annotate_session(log_path, screenshots_dir, output_dir)
    print(f"Annotated {len(annotated)} frames → {output_dir}")

    if args.html and annotated:
        build_html_replay(log_path, output_dir, output_dir / "replay.html")


if __name__ == "__main__":
    main()
