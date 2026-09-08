"""Quick real-API confirmation that CohereParseOcrProvider runs pages in parallel.

Compares workers=1 vs workers=4 on the first N pages of the Haas PDF. Uses the
real key from COHERE_API_KEY. Non-fatal if rate limits interfere.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docparse import providers  # noqa: E402
from docparse.providers import DocumentSource  # noqa: E402

PDF = (
    r"C:/Users/spatt/Desktop/docparse/tests/Haas_1992_IntroductionEpistemicCommunities/"
    "Haas - Introduction epistemic communities and international policy coordination.pdf"
)
N_PAGES = 6
KEY = os.environ.get("COHERE_API_KEY", "")


def time_extract(workers: int) -> float:
    ocr = providers.get_ocr_provider("cohere-parse", api_key=KEY)
    ocr._workers = workers
    src = DocumentSource.from_path(PDF)
    t0 = time.time()
    text = ocr.extract(src)
    return time.time() - t0, len(text)


if __name__ == "__main__":
    if not KEY:
        print("No COHERE_API_KEY"); sys.exit(1)
    import pypdfium2 as pdfium
    # Limit pages by patching the renderer at module level
    from docparse.providers import cohere as cohere_mod
    import types

    orig = cohere_mod._render_pdf_pages
    def limited(pdf_bytes, dpi):
        return orig(pdf_bytes, dpi)[:N_PAGES]
    cohere_mod._render_pdf_pages = limited

    t1, l1 = time_extract(1)
    print(f"workers=1: {t1:.1f}s for {N_PAGES} pages ({t1/N_PAGES:.1f}s/page), {l1} chars")
    t4, l4 = time_extract(4)
    print(f"workers=4: {t4:.1f}s for {N_PAGES} pages ({t4/N_PAGES:.1f}s/page), {l4} chars")
    print(f"speedup: {t1/t4:.2f}x" if t4 > 0 else "speedup: n/a")
