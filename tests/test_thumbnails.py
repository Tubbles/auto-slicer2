import base64
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from auto_slicer.thumbnails import (
    encode_thumbnail,
    find_header_end,
    inject_thumbnails,
    generate_thumbnails,
    render_stl_thumbnail,
    BASE64_LINE_WIDTH,
)


# Minimal valid 1x1 red PNG (67 bytes)
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
    b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_encode_thumbnail(tmp_path):
    png_path = tmp_path / "thumb.png"
    png_path.write_bytes(TINY_PNG)

    result = encode_thumbnail(png_path, 32, 32)

    lines = result.strip().split("\n")
    expected_b64 = base64.b64encode(TINY_PNG).decode("ascii")

    # Header format
    assert lines[0] == f"; thumbnail begin 32x32 {len(expected_b64)}"
    # Footer
    assert lines[-1] == "; thumbnail end"
    # Body lines start with "; "
    for line in lines[1:-1]:
        assert line.startswith("; ")
    # Reconstructed base64 matches
    reconstructed = "".join(line[2:] for line in lines[1:-1])
    assert reconstructed == expected_b64


def test_encode_thumbnail_line_length(tmp_path):
    # Use a larger payload to ensure multi-line splitting
    big_png = TINY_PNG * 20
    png_path = tmp_path / "big.png"
    png_path.write_bytes(big_png)

    result = encode_thumbnail(png_path, 300, 300)

    for line in result.strip().split("\n"):
        if line.startswith("; thumbnail begin") or line == "; thumbnail end":
            continue
        # "; " prefix (2 chars) + base64 data (max 78 chars)
        assert len(line) <= 2 + BASE64_LINE_WIDTH


def test_inject_thumbnails_after_header(tmp_path):
    gcode_path = tmp_path / "test.gcode"
    gcode_path.write_text(";FLAVOR:Marlin\n;TIME:100\nG28\nG1 X0 Y0\n")

    inject_thumbnails(gcode_path, "; thumbnail begin 32x32 100\n; AAAA\n; thumbnail end\n")

    content = gcode_path.read_text()
    # Header comments come first
    assert content.startswith(";FLAVOR:Marlin\n")
    # Thumbnails come after header but before gcode commands
    assert content.index(";FLAVOR:Marlin") < content.index("; thumbnail begin")
    assert content.index("; thumbnail end") < content.index("G28")


def test_inject_thumbnails_no_header(tmp_path):
    """When gcode has no comment header, thumbnails go at the top."""
    gcode_path = tmp_path / "test.gcode"
    gcode_path.write_text("G28\nG1 X0 Y0\n")

    inject_thumbnails(gcode_path, "; thumbnail begin 32x32 100\n; AAAA\n; thumbnail end\n")

    content = gcode_path.read_text()
    assert content.index("; thumbnail begin") < content.index("G28")


def test_find_header_end():
    lines = [";FLAVOR:Marlin\n", ";TIME:100\n", "G28\n", "G1 X0\n"]
    assert find_header_end(lines) == 2


def test_find_header_end_skips_blank_lines():
    lines = [";FLAVOR:Marlin\n", "\n", ";TIME:100\n", "G28\n"]
    assert find_header_end(lines) == 3


def test_find_header_end_all_comments():
    lines = [";FLAVOR:Marlin\n", ";TIME:100\n"]
    assert find_header_end(lines) == 2


def test_find_header_end_no_comments():
    lines = ["G28\n", "G1 X0\n"]
    assert find_header_end(lines) == 0


def test_generate_thumbnails_openscad_missing(tmp_path):
    stl_path = tmp_path / "model.stl"
    stl_path.write_text("solid cube endsolid cube")

    with patch("auto_slicer.thumbnails.subprocess.run", side_effect=FileNotFoundError):
        result = generate_thumbnails(stl_path, tmp_path)

    assert result == ""


def test_render_stl_thumbnail_command(tmp_path):
    stl_path = tmp_path / "model.stl"
    output_path = tmp_path / "thumb.png"

    mock_result = MagicMock()
    mock_result.returncode = 0

    with patch("auto_slicer.thumbnails.subprocess.run", return_value=mock_result) as mock_run:
        success = render_stl_thumbnail(stl_path, output_path, 300, 300)

    assert success is True
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "openscad"
    assert "-o" in cmd
    assert str(output_path) in cmd
    assert "--imgsize=300,300" in cmd
    assert "--autocenter" in cmd
    assert "--viewall" in cmd
    assert f'import("{stl_path}");' in cmd
    assert "/dev/null" in cmd


def test_render_stl_thumbnail_timeout(tmp_path):
    stl_path = tmp_path / "model.stl"
    output_path = tmp_path / "thumb.png"

    import subprocess as sp
    with patch("auto_slicer.thumbnails.subprocess.run", side_effect=sp.TimeoutExpired("openscad", 30)):
        success = render_stl_thumbnail(stl_path, output_path, 32, 32)

    assert success is False
