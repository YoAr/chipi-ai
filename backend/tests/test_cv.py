import io

import docx
import pytest

from app.cv import extract_text


def test_txt_and_md():
    assert extract_text("cv.txt", b"Hello   world\n\n\n\nBye") == "Hello world\n\nBye"
    assert extract_text("CV.MD", b"# Me") == "# Me"


def test_docx():
    d = docx.Document()
    d.add_paragraph("Jane  Doe")
    d.add_paragraph("Python engineer")
    buf = io.BytesIO()
    d.save(buf)
    out = extract_text("cv.docx", buf.getvalue())
    assert "Jane Doe" in out and "Python engineer" in out


def test_unsupported():
    with pytest.raises(ValueError, match="Unsupported file type"):
        extract_text("cv.exe", b"x")


def test_corrupt_pdf_and_docx():
    with pytest.raises(ValueError):
        extract_text("cv.pdf", b"not a pdf")
    with pytest.raises(ValueError):
        extract_text("cv.docx", b"not a docx")


def test_pdf_blank_page():
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(100, 100)
    buf = io.BytesIO()
    w.write(buf)
    assert extract_text("cv.pdf", buf.getvalue()) == ""


def test_cap():
    assert len(extract_text("a.txt", b"a" * 300_000)) == 200_000
