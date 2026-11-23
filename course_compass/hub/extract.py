"""Turn course files into markdown text the notes index can chunk.

Every page or slide becomes its own `## Page N` / `## Slide N: title` section,
so a cited answer points at the exact page of the lecture slides.

  .md .markdown .txt   read as-is
  .html .htm           tags stripped, headings kept
  .pdf                 pypdf if installed, else the `pdftotext` binary; scanned PDFs
                       with no text layer go through `tesseract` OCR when it is installed
  .pptx                slide text from the slide XML (standard library zipfile)
  .docx                paragraph text from the document XML
  .ipynb               markdown and code cells
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from .htmltext import to_text

TEXT_SUFFIXES = {".md", ".markdown", ".txt"}
SUPPORTED = TEXT_SUFFIXES | {".html", ".htm", ".pdf", ".pptx", ".docx", ".ipynb"}
MAX_TEXT_CHARS = 600_000            # per document, after extraction
MAX_ZIP_MEMBER = 30_000_000         # uncompressed bytes per XML member (zip-bomb guard)
MAX_ZIP_TOTAL = 120_000_000
MIN_TEXT_PER_PAGE = 20              # fewer characters than this per page means "probably scanned"

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ExtractError(Exception):
    pass


@dataclass
class Extracted:
    markdown: str
    status: str          # ok, empty, needs-ocr
    method: str
    pages: int = 0


def title_from_name(name: str) -> str:
    stem = Path(name).stem
    return re.sub(r"[_\-]+", " ", stem).strip() or "Untitled"


def _cap(text: str) -> str:
    return text[:MAX_TEXT_CHARS]


def _status(markdown: str, pages: int = 0) -> str:
    body = re.sub(r"^#.*$", "", markdown, flags=re.MULTILINE)
    letters = len(re.findall(r"\w", body))
    if letters == 0:
        return "empty"
    if pages and letters / pages < MIN_TEXT_PER_PAGE:
        return "needs-ocr"
    return "ok"


def extract_file(path: Path, title: str | None = None) -> Extracted:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED:
        raise ExtractError(f"unsupported file type {suffix or '(none)'}")
    title = title or title_from_name(path.name)
    if suffix in TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.lstrip().startswith("#"):
            text = f"# {title}\n\n{text}"
        return Extracted(_cap(text), _status(text), "text")
    if suffix in (".html", ".htm"):
        body = to_text(path.read_text(encoding="utf-8", errors="replace"), markdown_headings=True)
        text = f"# {title}\n\n{body}"
        return Extracted(_cap(text), _status(text), "html")
    if suffix == ".pdf":
        return extract_pdf(path, title)
    if suffix == ".pptx":
        return extract_pptx(path, title)
    if suffix == ".docx":
        return extract_docx(path, title)
    return extract_ipynb(path, title)


# ---------------------------------------------------------------- PDF

def pdf_pages(path: Path) -> tuple[list[str], str]:
    """Text of each page and the method used. Raises ExtractError when no PDF reader is available."""
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        PdfReader = None
    if PdfReader is not None:
        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception:
                    raise ExtractError("PDF is password-protected") from None
            return [(page.extract_text() or "") for page in reader.pages[:1000]], "pypdf"
        except ExtractError:
            raise
        except Exception as exc:   # malformed PDFs raise many different pypdf errors
            if not shutil.which("pdftotext"):
                raise ExtractError(f"could not read PDF ({type(exc).__name__})") from None
    binary = shutil.which("pdftotext")
    if not binary:
        raise ExtractError("PDF text needs `pip install pypdf` or the `pdftotext` tool (brew install poppler)")
    try:
        done = subprocess.run([binary, "-layout", "-enc", "UTF-8", str(path), "-"], capture_output=True,
                              timeout=120, check=False)
    except subprocess.TimeoutExpired:
        raise ExtractError("pdftotext timed out") from None
    if done.returncode != 0:
        raise ExtractError("pdftotext could not read this PDF")
    text = done.stdout.decode("utf-8", errors="replace")
    return text.split("\f"), "pdftotext"


def ocr_pages(path: Path, max_pages: int = 80) -> list[str] | None:
    """OCR a scanned PDF with pdftoppm + tesseract, if both are installed. None when they are not."""
    pdftoppm, tesseract = shutil.which("pdftoppm"), shutil.which("tesseract")
    if not (pdftoppm and tesseract):
        return None
    pages: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run([pdftoppm, "-r", "200", "-l", str(max_pages), "-png", str(path), f"{tmp}/p"],
                           capture_output=True, timeout=600, check=True)
            for image in sorted(Path(tmp).glob("p-*.png")):
                done = subprocess.run([tesseract, str(image), "-", "--psm", "3"], capture_output=True,
                                      timeout=120, check=False)
                pages.append(done.stdout.decode("utf-8", errors="replace"))
        except (subprocess.SubprocessError, OSError):
            return None
    return pages


def _pages_markdown(title: str, pages: list[str], label: str = "Page") -> str:
    parts = [f"# {title}"]
    for number, text in enumerate(pages, start=1):
        cleaned = "\n".join(line.rstrip() for line in text.splitlines())
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        # a line starting with '#' inside a page would be read as a heading
        cleaned = re.sub(r"^#", r"\\#", cleaned, flags=re.MULTILINE)
        if cleaned:
            parts.append(f"## {label} {number}\n\n{cleaned}")
    return "\n\n".join(parts)


def extract_pdf(path: Path, title: str) -> Extracted:
    pages, method = pdf_pages(path)
    markdown = _pages_markdown(title, pages)
    status = _status(markdown, len(pages))
    if status in ("needs-ocr", "empty"):
        scanned = ocr_pages(path)
        if scanned:
            markdown = _pages_markdown(title, scanned)
            return Extracted(_cap(markdown), _status(markdown), "ocr", len(scanned))
    return Extracted(_cap(markdown), status, method, len(pages))


# ---------------------------------------------------------------- Office XML

def _safe_zip(path: Path) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ExtractError("not a valid Office file") from None
    total = sum(info.file_size for info in archive.infolist())
    if total > MAX_ZIP_TOTAL:
        archive.close()
        raise ExtractError("Office file expands to more than 120 MB; skipped")
    return archive


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_ZIP_MEMBER:
        raise ExtractError(f"{name} is too large")
    return archive.read(name)


def _xml(data: bytes) -> ElementTree.Element:
    # ElementTree does not expand external entities; refuse DTDs outright anyway.
    if b"<!DOCTYPE" in data[:2000] or b"<!ENTITY" in data:
        raise ExtractError("XML with a DTD is not accepted")
    return ElementTree.fromstring(data)


def extract_pptx(path: Path, title: str) -> Extracted:
    with _safe_zip(path) as archive:
        names = [n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        names.sort(key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        sections = [f"# {title}"]
        for number, name in enumerate(names, start=1):
            root = _xml(_read_member(archive, name))
            paragraphs = []
            for para in root.iter(f"{_A}p"):
                text = "".join(run.text or "" for run in para.iter(f"{_A}t")).strip()
                if text:
                    paragraphs.append(text)
            if not paragraphs:
                continue
            head, rest = paragraphs[0], paragraphs[1:]
            body = "\n".join(f"- {line}" for line in rest)
            sections.append(f"## Slide {number}: {head[:80]}\n\n{body}".rstrip())
    markdown = "\n\n".join(sections)
    return Extracted(_cap(markdown), _status(markdown), "pptx", len(names))


def extract_docx(path: Path, title: str) -> Extracted:
    with _safe_zip(path) as archive:
        try:
            root = _xml(_read_member(archive, "word/document.xml"))
        except KeyError:
            raise ExtractError("no document body in this .docx") from None
    lines = [f"# {title}", ""]
    for para in root.iter(f"{_W}p"):
        text = "".join(t.text or "" for t in para.iter(f"{_W}t")).strip()
        if not text:
            continue
        style = para.find(f"{_W}pPr/{_W}pStyle")
        level = style.get(f"{_W}val", "") if style is not None else ""
        match = re.match(r"Heading(\d)", level)
        if match:
            lines += ["", "#" * min(4, int(match.group(1)) + 1) + " " + text, ""]
        else:
            lines.append(text)
            lines.append("")
    markdown = "\n".join(lines)
    return Extracted(_cap(markdown), _status(markdown), "docx")


def extract_ipynb(path: Path, title: str) -> Extracted:
    try:
        notebook = json.loads(path.read_text(encoding="utf-8"))
        cells = notebook.get("cells", [])
    except (ValueError, AttributeError):
        raise ExtractError("not a valid notebook") from None
    parts = [f"# {title}"]
    for cell in cells:
        source = cell.get("source", "")
        source = "".join(source) if isinstance(source, list) else str(source)
        if cell.get("cell_type") == "markdown":
            parts.append(source)
        elif cell.get("cell_type") == "code" and source.strip():
            parts.append("```\n" + source + "\n```")
    markdown = "\n\n".join(parts)
    return Extracted(_cap(markdown), _status(markdown), "ipynb")
