"""题面/数据文件处理：xlsx 预览读取、zip 解压入库、data/ 递归列举。"""

import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest

from magent.engine import _extract_zip, _list_problem_files, init_project
from magent.tools import ToolBox, ToolError


def _make_xlsx(path: Path) -> None:
    """用标准库拼一个最小可用 xlsx（含共享字符串与两个工作表）。"""
    NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "xl/sharedStrings.xml",
            f'<?xml version="1.0"?><sst xmlns="{NS}"><si><t>城市</t></si><si><t>销量</t></si><si><t>北京</t></si></sst>',
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            f'<?xml version="1.0"?><worksheet xmlns="{NS}"><sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>42.5</v></c></row>'
            "</sheetData></worksheet>",
        )
        zf.writestr("xl/worksheets/sheet2.xml", f'<?xml version="1.0"?><worksheet xmlns="{NS}"><sheetData/></worksheet>')


@pytest.fixture
def box(tmp_path):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    return ToolBox(root)


def test_read_xlsx_preview(box):
    xlsx = box.root / "data" / "附件1.xlsx"
    _make_xlsx(xlsx)
    out = box.execute("read_file", '{"path": "data/附件1.xlsx"}')
    assert "城市 | 销量" in out
    assert "北京 | 42.5" in out
    assert "Excel 内容" in out


def test_read_xlsx_empty_sheet_no_crash(box):
    xlsx = box.root / "data" / "空表.xlsx"
    _make_xlsx(xlsx)  # sheet2 是空表
    out = box.execute("read_file", '{"path": "data/空表.xlsx"}')
    assert "sheet2" in out and "前 0 行" in out


def test_read_xls_and_zip_hints(box):
    (box.root / "data" / "旧表.xls").write_bytes(b"x")
    (box.root / "data" / "附件.zip").write_bytes(b"x")
    assert ".xlsx/.csv" in box.execute("read_file", '{"path": "data/旧表.xls"}')
    assert "zipfile" in box.execute("read_file", '{"path": "data/附件.zip"}')


def test_extract_zip_blocks_traversal(tmp_path):
    dest = tmp_path / "data"
    dest.mkdir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("正常/数据.csv", "a,b\n1,2\n")
        zf.writestr("../逃逸.txt", "bad")
    written = _extract_zip(buf.getvalue(), dest)
    assert written == ["正常/数据.csv"]
    assert (dest / "正常" / "数据.csv").is_file()
    assert not (tmp_path / "逃逸.txt").exists()


def test_init_project_extracts_zip_and_lists_recursively(tmp_path):
    skills = Path("magent/skills").resolve()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("数据/表1.csv", "x\n1\n")
    root = tmp_path / "题目A"
    init_project(
        root=root,
        title="含数据附件",
        prefs={},
        problem_files=[("题目.pdf", b"%PDF-1.4 fake"), ("附件.zip", buf.getvalue())],
        skills_root=skills,
    )
    assert (root / "data" / "题目.pdf").is_file()
    assert (root / "data" / "数据" / "表1.csv").is_file()
    assert not (root / "data" / "附件.zip").exists()  # 解压成功不保留压缩包
    listed = _list_problem_files(root)
    assert "题目.pdf" in listed and "数据/表1.csv" in listed


def test_data_dir_still_write_denied(box):
    with pytest.raises(ToolError):
        box.write_file("data/偷偷改.csv", "x")
