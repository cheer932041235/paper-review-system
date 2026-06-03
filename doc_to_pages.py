"""
Document to Page Images Converter
Converts DOCX/PDF files to per-page PNG images for visual review.

Usage:
    python doc_to_pages.py <input_file> [--output-dir <dir>] [--dpi <dpi>] [--pages <range>]

Examples:
    python doc_to_pages.py thesis.docx
    python doc_to_pages.py thesis.pdf --dpi 200 --pages 1-10
    python doc_to_pages.py thesis.docx --output-dir ./review_pages
"""

import argparse
import os
import sys
import time
from pathlib import Path


def docx_to_pdf(docx_path: str, pdf_path: str) -> str:
    """Convert DOCX to PDF using Microsoft Word COM automation (Windows only)."""
    import win32com.client
    import pythoncom

    pythoncom.CoInitialize()
    word = None
    doc = None
    try:
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        word.DisplayAlerts = False

        abs_docx = os.path.abspath(docx_path)
        abs_pdf = os.path.abspath(pdf_path)

        print(f"  Opening: {abs_docx}")
        doc = word.Documents.Open(abs_docx, ReadOnly=True)
        print(f"  Saving PDF: {abs_pdf}")
        # wdFormatPDF = 17
        doc.SaveAs2(abs_pdf, FileFormat=17)
        print(f"  PDF saved successfully.")
        return abs_pdf
    finally:
        if doc:
            doc.Close(False)
        if word:
            word.Quit()
        pythoncom.CoUninitialize()


def pdf_to_pages(pdf_path: str, output_dir: str, dpi: int = 150,
                 page_range: tuple = None) -> list:
    """Convert PDF pages to PNG images using PyMuPDF."""
    import fitz  # PyMuPDF

    os.makedirs(output_dir, exist_ok=True)

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    print(f"  PDF has {total_pages} pages, rendering at {dpi} DPI...")

    start_page = (page_range[0] - 1) if page_range else 0
    end_page = page_range[1] if page_range else total_pages

    start_page = max(0, start_page)
    end_page = min(total_pages, end_page)

    output_files = []
    zoom = dpi / 72.0  # 72 is default PDF DPI
    mat = fitz.Matrix(zoom, zoom)

    for i in range(start_page, end_page):
        page = doc[i]
        pix = page.get_pixmap(matrix=mat)
        page_num = i + 1
        out_file = os.path.join(output_dir, f"page_{page_num:03d}.png")
        pix.save(out_file)
        output_files.append(out_file)
        print(f"    Page {page_num}/{total_pages} -> {out_file}")

    doc.close()
    print(f"  Done: {len(output_files)} page images saved to {output_dir}")
    return output_files


def main():
    parser = argparse.ArgumentParser(description="Convert document to page images for visual review")
    parser.add_argument("input_file", help="Input DOCX or PDF file")
    parser.add_argument("--output-dir", "-o", help="Output directory for page images (default: <input>_pages/)")
    parser.add_argument("--dpi", type=int, default=150, help="Render DPI (default: 150, use 200+ for detailed review)")
    parser.add_argument("--pages", help="Page range, e.g. '1-10' or '5-5' for single page")
    parser.add_argument("--skip-pdf", action="store_true", help="Skip DOCX->PDF conversion if PDF already exists")
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    # Determine output directory
    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = str(input_path.parent / f"{input_path.stem}_pages")

    # Parse page range
    page_range = None
    if args.pages:
        parts = args.pages.split("-")
        if len(parts) == 2:
            page_range = (int(parts[0]), int(parts[1]))
        elif len(parts) == 1:
            p = int(parts[0])
            page_range = (p, p)

    suffix = input_path.suffix.lower()
    pdf_path = str(input_path)

    # Step 1: Convert DOCX to PDF if needed
    if suffix in (".docx", ".doc"):
        pdf_path = str(input_path.parent / f"{input_path.stem}.pdf")

        if args.skip_pdf and os.path.exists(pdf_path):
            print(f"[1/2] Skipping conversion, PDF exists: {pdf_path}")
        else:
            print(f"[1/2] Converting DOCX -> PDF...")
            start = time.time()
            pdf_path = docx_to_pdf(str(input_path), pdf_path)
            elapsed = time.time() - start
            print(f"  Conversion took {elapsed:.1f}s")
    elif suffix == ".pdf":
        print(f"[1/2] Input is PDF, no conversion needed.")
    else:
        print(f"Error: Unsupported format: {suffix}")
        sys.exit(1)

    # Step 2: Render PDF pages to images
    print(f"[2/2] Rendering pages to images...")
    start = time.time()
    output_files = pdf_to_pages(pdf_path, output_dir, dpi=args.dpi, page_range=page_range)
    elapsed = time.time() - start
    print(f"  Rendering took {elapsed:.1f}s")

    # Summary
    print(f"\n{'='*60}")
    print(f"  Total pages rendered: {len(output_files)}")
    print(f"  Output directory:     {output_dir}")
    print(f"  DPI:                  {args.dpi}")
    print(f"{'='*60}")

    return output_files


if __name__ == "__main__":
    main()
