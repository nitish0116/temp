"""High-quality PDF/EPUB-to-Markdown conversion using PyMuPDF4LLM."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from time import perf_counter

SUPPORTED_EXTENSIONS = {".pdf", ".epub"}


@dataclass(slots=True)
class ConversionOptions:
    layout_mode: str = "fast"
    ocr_mode: str = "auto"
    ocr_language: str = "eng"
    header: bool = False
    footer: bool = False
    image_mode: str = "ignore"
    image_format: str = "png"
    pages: list[int] | None = None
    show_progress: bool = True

    def __post_init__(self):
        if self.layout_mode not in {"fast", "smart"}:
            raise ValueError("layout_mode must be one of: fast, smart")
        if self.ocr_mode not in {"auto", "off", "force"}:
            raise ValueError("ocr_mode must be one of: auto, off, force")
        if self.image_mode not in {"ignore", "write", "embed"}:
            raise ValueError("image_mode must be one of: ignore, write, embed")


@dataclass(slots=True)
class ConversionResult:
    source: str
    markdown: str
    report: str
    pages: int
    characters: int
    elapsed_seconds: float
    options: dict


def parse_page_spec(spec: str | None) -> list[int] | None:
    """Parse human-facing 1-based pages such as ``1-3,7`` to zero-based indexes."""
    if not spec:
        return None
    pages: set[int] = set()
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start < 1 or end < start:
                raise ValueError(f"Invalid page range: {item}")
            pages.update(range(start - 1, end))
        else:
            page = int(item)
            if page < 1:
                raise ValueError(f"Invalid page number: {item}")
            pages.add(page - 1)
    return sorted(pages)


def readable_output_name(source: str | Path) -> str:
    """Create a readable Markdown filename without release/source tags."""
    name = Path(source).stem
    name = re.sub(r"(?:\s*(?:\[[^\[\]]+\]|\{[^{}]+\}))+\s*$", "", name)
    name = name.replace("_", " ")
    name = re.sub(r"\s*[-–—]\s*", " - ", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .-_")
    return f"{name or 'Converted document'}.md"


class PDFToMarkdownConverter:
    def __init__(self, options: ConversionOptions | None = None):
        self.options = options or ConversionOptions()

    @staticmethod
    def _dependencies():
        try:
            import pymupdf
            import pymupdf4llm
        except ImportError as exc:
            raise RuntimeError(
                "PDF conversion dependencies are missing. Run: "
                "pip install -r pdf-md/requirements.txt"
            ) from exc
        return pymupdf, pymupdf4llm

    def convert(self, source: str | Path, output: str | Path) -> ConversionResult:
        source_path = Path(source).resolve()
        output_path = Path(output).resolve()
        extension = source_path.suffix.casefold()
        if not source_path.is_file() or extension not in SUPPORTED_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            raise ValueError(
                f"Input must be a supported document ({supported}): {source_path}"
            )

        pymupdf, pymupdf4llm = self._dependencies()
        # Neural layout analysis can materially improve complex tables and
        # columns, but is far slower for full novels. Forced OCR necessarily
        # enables it because legacy mode has no OCR support.
        effective_layout = (
            "smart"
            if self.options.layout_mode == "smart" or self.options.ocr_mode == "force"
            else "fast"
        )
        pymupdf4llm.use_layout(effective_layout == "smart")
        started = perf_counter()
        with pymupdf.open(source_path) as document:
            total_pages = document.page_count
            pages = self.options.pages
            if pages and pages[-1] >= total_pages:
                raise ValueError(
                    f"Requested page {pages[-1] + 1}, but the PDF has {total_pages} pages"
                )

            image_dir = output_path.parent / f"{output_path.stem}_images"
            is_pdf = extension == ".pdf"
            arguments = {
                "pages": pages,
                "ignore_images": self.options.image_mode == "ignore",
                "write_images": self.options.image_mode == "write",
                "embed_images": self.options.image_mode == "embed",
                "image_path": str(image_dir),
                "image_format": self.options.image_format,
                "show_progress": self.options.show_progress,
                "page_separators": False,
            }
            if effective_layout == "smart":
                arguments.update(
                    header=self.options.header if is_pdf else True,
                    footer=self.options.footer if is_pdf else True,
                    use_ocr=self.options.ocr_mode != "off" if is_pdf else False,
                    force_ocr=self.options.ocr_mode == "force" if is_pdf else False,
                    ocr_language=self.options.ocr_language,
                )
            markdown = pymupdf4llm.to_markdown(document, **arguments)

            # Fast mode intentionally avoids the expensive neural/OCR stack.
            # If automatic OCR was requested and extraction produced nothing,
            # retry this document using smart mode rather than emitting an
            # apparently successful empty Markdown file.
            if (
                is_pdf
                and effective_layout == "fast"
                and self.options.ocr_mode == "auto"
                and not markdown.strip()
            ):
                effective_layout = "smart"
                pymupdf4llm.use_layout(True)
                arguments.update(
                    header=self.options.header,
                    footer=self.options.footer,
                    use_ocr=True,
                    force_ocr=False,
                    ocr_language=self.options.ocr_language,
                )
                markdown = pymupdf4llm.to_markdown(document, **arguments)

        markdown = markdown.strip() + "\n" if markdown.strip() else ""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")

        elapsed = round(perf_counter() - started, 3)
        report_path = output_path.with_suffix(".conversion.json")
        result = ConversionResult(
            source=str(source_path),
            markdown=str(output_path),
            report=str(report_path),
            pages=len(self.options.pages) if self.options.pages else total_pages,
            characters=len(markdown),
            elapsed_seconds=elapsed,
            options={
                **asdict(self.options),
                "effective_layout": effective_layout,
                "effective_ocr_mode": (
                    self.options.ocr_mode
                    if is_pdf and effective_layout == "smart"
                    else "off"
                ),
            },
        )
        report_path.write_text(
            json.dumps(
                {
                    **asdict(result),
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return result
