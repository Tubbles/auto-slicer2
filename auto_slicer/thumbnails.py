import base64
import subprocess
from pathlib import Path


THUMBNAIL_SIZES = [(32, 32), (300, 300)]
OPENSCAD_TIMEOUT = 30
BASE64_LINE_WIDTH = 78


def render_stl_thumbnail(stl_path: Path, output_path: Path, width: int, height: int) -> bool:
    """Render an STL file to a PNG thumbnail using OpenSCAD."""
    cmd = [
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
    return "\n".join(blocks)


def inject_thumbnails(gcode_path: Path, thumbnail_comments: str) -> None:
    """Prepend thumbnail comments to the beginning of a gcode file."""
    original = gcode_path.read_text()
    gcode_path.write_text(thumbnail_comments + "\n" + original)
