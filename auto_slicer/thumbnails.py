import base64
import subprocess
from pathlib import Path


THUMBNAIL_SIZES = [(32, 32), (300, 300)]
OPENSCAD_TIMEOUT = 30
BASE64_LINE_WIDTH = 78


def render_stl_thumbnail(stl_path: Path, output_path: Path, width: int, height: int) -> bool:
    """Render an STL file to a PNG thumbnail using OpenSCAD."""
    cmd = [
        "xvfb-run", "--auto-servernum",
        "openscad",
        "-o", str(output_path),
        f"--imgsize={width},{height}",
        "--autocenter",
        "--viewall",
        "-D", f'import("{stl_path}");',
        "/dev/null",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=OPENSCAD_TIMEOUT)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def encode_thumbnail(png_path: Path, width: int, height: int) -> str:
    """Read a PNG file and format it as a gcode thumbnail comment block."""
    raw_b64 = base64.b64encode(png_path.read_bytes()).decode("ascii")
    lines = [raw_b64[i:i + BASE64_LINE_WIDTH] for i in range(0, len(raw_b64), BASE64_LINE_WIDTH)]
    header = f"; thumbnail begin {width}x{height} {len(raw_b64)}"
    footer = "; thumbnail end"
    body = "\n".join(f"; {line}" for line in lines)
    return f"{header}\n{body}\n{footer}\n"


def generate_thumbnails(stl_path: Path, tmp_dir: Path) -> str:
    """Render and encode thumbnails for all sizes. Returns gcode comments or empty string."""
    blocks = []
    for width, height in THUMBNAIL_SIZES:
        png_path = tmp_dir / f"thumb_{width}x{height}.png"
        if not render_stl_thumbnail(stl_path, png_path, width, height):
            print(f"[Thumbnail] Failed to render {width}x{height}")
            return ""
        blocks.append(encode_thumbnail(png_path, width, height))
    return ";\n\n;\n".join(blocks)


def find_header_end(lines: list[str]) -> int:
    """Find the line index where the initial comment header ends.

    Skips blank lines that appear between comment lines.
    """
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith(";"):
            return i
    return len(lines)


def inject_thumbnails(gcode_path: Path, thumbnail_comments: str) -> None:
    """Insert thumbnail comments after the CuraEngine comment header."""
    lines = gcode_path.read_text().splitlines(keepends=True)
    pos = find_header_end(lines)
    header = "".join(lines[:pos])
    body = "".join(lines[pos:])
    gcode_path.write_text(header + ";\n" + thumbnail_comments + ";\n\n" + body)
