"""External tool discovery (pandoc, WeasyPrint, PDF readers, git, epubcheck) with install help."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .project import BookToolsError

# Where Windows installers commonly put things when PATH hasn't refreshed yet.
_PANDOC_FALLBACKS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Pandoc" / "pandoc.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Pandoc" / "pandoc.exe",
]
_MSYS2_DLL_DIRS = [Path(r"C:\msys64\ucrt64\bin"), Path(r"C:\msys64\mingw64\bin")]


def find_pandoc() -> str | None:
    exe = shutil.which("pandoc")
    if exe:
        return exe
    if sys.platform == "win32":
        for p in _PANDOC_FALLBACKS:
            if p.is_file():
                return str(p)
    return None


def pandoc() -> tuple[str, tuple[int, ...]]:
    exe = find_pandoc()
    if not exe:
        raise BookToolsError(
            "pandoc not found.\n"
            "  Windows:  winget install JohnMacFarlane.Pandoc   (then open a NEW terminal)\n"
            "  macOS:    brew install pandoc\n"
            "  Other:    https://pandoc.org/installing.html")
    out = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout
    m = re.search(r"pandoc(?:\.exe)?\s+(\d+(?:\.\d+)*)", out)
    return exe, (tuple(int(x) for x in m.group(1).split(".")) if m else (0,))


def no_highlight_args(version: tuple[int, ...]) -> list[str]:
    return ["--syntax-highlighting=none"] if version >= (3, 8) else ["--no-highlight"]


def _prepare_weasyprint_dlls() -> str | None:
    """On Windows, point WeasyPrint at MSYS2's Pango DLLs if the user hasn't already."""
    if sys.platform != "win32":
        return None
    current = os.environ.get("WEASYPRINT_DLL_DIRECTORIES")
    if current:
        return current
    for d in _MSYS2_DLL_DIRS:
        if (d / "libpango-1.0-0.dll").is_file():
            os.environ["WEASYPRINT_DLL_DIRECTORIES"] = str(d)
            return str(d)
    return None


def weasyprint():
    """Import and return the weasyprint module, or raise with install help."""
    dll_dir = _prepare_weasyprint_dlls()
    try:
        import weasyprint as wp  # noqa: WPS433
        return wp
    except ImportError as exc:
        reason = f"Python package not installed ({exc})"
    except OSError as exc:
        reason = f"native Pango libraries failed to load ({exc})"
    raise BookToolsError(
        f"WeasyPrint unavailable: {reason}.\n"
        "  1. python -m pip install weasyprint\n"
        "  2. Windows only — Pango runtime via MSYS2:\n"
        "       winget install MSYS2.MSYS2\n"
        '       C:\\msys64\\usr\\bin\\bash.exe -lc "pacman -S --noconfirm mingw-w64-ucrt-x86_64-pango"\n'
        "     booktools finds C:\\msys64\\ucrt64\\bin automatically; elsewhere set WEASYPRINT_DLL_DIRECTORIES.\n"
        f"  (DLL dir in use: {dll_dir or 'none found'})\n"
        "  Docs: https://doc.courtbouillon.org/weasyprint/stable/first_steps.html")


def pdf_page_count(pdf: Path) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(pdf)).pages)
    except ImportError:
        pass
    try:
        import fitz  # PyMuPDF
        with fitz.open(str(pdf)) as doc:
            return doc.page_count
    except ImportError:
        raise BookToolsError("Need pypdf or PyMuPDF to count PDF pages.\n  python -m pip install pypdf") from None


def doctor(project=None) -> int:
    """Print the status of every dependency. Returns 0 if all required ones work.

    Inside a book with a `research:` block, also checks the local AI server and configured models."""
    ok = True
    rows: list[tuple[str, str, str]] = []

    try:
        exe, ver = pandoc()
        rows.append(("pandoc", "OK", f"{'.'.join(map(str, ver))}  {exe}"))
    except BookToolsError as exc:
        ok = False
        rows.append(("pandoc", "MISSING", str(exc).splitlines()[0]))

    try:
        wp = weasyprint()
        rows.append(("weasyprint", "OK", f"{wp.__version__}  (DLLs: {os.environ.get('WEASYPRINT_DLL_DIRECTORIES', 'system')})"))
    except BookToolsError as exc:
        ok = False
        rows.append(("weasyprint", "MISSING", str(exc).splitlines()[0]))

    reader = None
    for mod, label in (("pypdf", "pypdf"), ("fitz", "pymupdf")):
        try:
            m = __import__(mod)
            reader = f"{label} {getattr(m, '__version__', getattr(m, 'VersionBind', ''))}"
            break
        except ImportError:
            continue
    rows.append(("pdf reader", "OK" if reader else "MISSING", reader or "python -m pip install pypdf"))
    ok &= reader is not None

    import yaml
    rows.append(("pyyaml", "OK", yaml.__version__))
    git = shutil.which("git")
    rows.append(("git", "OK" if git else "MISSING", git or "winget install Git.Git (snapshots disabled)"))
    epc = shutil.which("epubcheck")
    rows.append(("epubcheck", "OK" if epc else "optional", epc or "not installed (EPUB validation skipped)"))

    for mod, label, hint in (("numpy", "numpy", ""), ("networkx", "networkx", ""), ("sklearn", "scikit-learn", ""),
                             ("trafilatura", "trafilatura", ""), ("docx", "python-docx", "optional (DOCX sources)")):
        try:
            m = __import__(mod)
            rows.append((label, "OK", getattr(m, "__version__", "")))
        except ImportError:
            required = not hint
            ok &= not required
            rows.append((label, "MISSING" if required else "optional", hint or f"python -m pip install {label}"))

    if project is not None and project.config.get("research"):
        from .research import config as rconfig
        from .research.llm import LocalAI
        try:
            cfg = rconfig.load(project, "doctor")
            have = LocalAI(cfg.endpoint).available()
            rows.append(("local AI", "OK", f"{cfg.endpoint} ({len(have)} models)"))
            for role, model in cfg.models.items():
                found = model in have
                ok &= found
                rows.append((f"  {role}", "OK" if found else "MISSING",
                             f"{model}" + ("" if found else "  (not in the server's model list; `lms ls`)")))
        except BookToolsError as exc:
            ok = False
            rows.append(("local AI", "ERROR", str(exc).splitlines()[0]))

    for name, status, detail in rows:
        print(f"{name:<14}{status:<9}{detail}")
    print("\nAll required tools ready." if ok else "\nSome required tools are missing (see above).")
    return 0 if ok else 1
