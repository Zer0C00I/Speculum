from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pdftranslator.config import Config

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"


class BabelDocError(RuntimeError):
    pass


@dataclass(frozen=True)
class BabelDocRequest:
    input_path: str
    output_path: str
    source_lang: str
    target_lang: str
    page_numbers: list[int]
    session_dir: str | None = None
    verbose: bool = False


def bundled_babeldoc_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "BabelDOC"


def uses_babeldoc(provider: str) -> bool:
    return provider == "babeldoc-deepseek"


def run_babeldoc(
    request: BabelDocRequest,
    log: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> Path:
    api_key = Config.deepseek_api_key().strip()
    if not api_key:
        raise BabelDocError("DEEPSEEK_API_KEY not set in .env")

    bundle_dir = bundled_babeldoc_dir()
    if not bundle_dir.is_dir():
        raise BabelDocError(f"Bundled BabelDOC directory not found: {bundle_dir}")

    if is_cancelled and is_cancelled():
        raise BabelDocError("Translation cancelled")

    output_dir = Path(tempfile.mkdtemp(prefix="pdftranslator-babeldoc-out-"))
    working_root = Path(tempfile.mkdtemp(prefix="pdftranslator-babeldoc-work-"))

    env = os.environ.copy()
    extra_pythonpath = str(bundle_dir)
    env["PYTHONPATH"] = (
        extra_pythonpath
        if not env.get("PYTHONPATH")
        else extra_pythonpath + os.pathsep + env["PYTHONPATH"]
    )

    cmd = [
        Config.babeldoc_python(),
        "-m",
        "babeldoc.main",
        "--files",
        request.input_path,
        "--pages",
        _page_numbers_to_ranges(request.page_numbers),
        "--lang-in",
        request.source_lang,
        "--lang-out",
        request.target_lang,
        "--output",
        str(output_dir),
        "--working-dir",
        str(working_root),
        "--openai",
        "--openai-model",
        DEEPSEEK_MODEL,
        "--openai-base-url",
        DEEPSEEK_BASE_URL,
        "--openai-api-key",
        api_key,
        "--no-dual",
        "--watermark-output-mode",
        "no_watermark",
    ]
    if request.verbose:
        cmd.append("--debug")

    if log:
        log("Starting BabelDOC translation")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(bundle_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    if log:
                        log(f"[BabelDOC] {line.rstrip()}")
                    if is_cancelled and is_cancelled():
                        proc.terminate()
                        break
            return_code = proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()

        if is_cancelled and is_cancelled():
            raise BabelDocError("Translation cancelled")

        if return_code != 0:
            raise BabelDocError(
                "BabelDOC failed. Ensure its dependencies are installed from "
                "BabelDOC/pyproject.toml and check the log for details."
            )

        result = _find_output_pdf(output_dir, Path(request.input_path).stem, request.target_lang)
        Path(request.output_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result, request.output_path)
        return Path(request.output_path)
    finally:
        with suppress(Exception):
            shutil.rmtree(output_dir)
        with suppress(Exception):
            shutil.rmtree(working_root)


def _find_output_pdf(output_dir: Path, stem: str, target_lang: str) -> Path:
    patterns = [
        f"{stem}.no_watermark.{target_lang}.mono.pdf",
        f"{stem}.{target_lang}.mono.pdf",
        f"*.{target_lang}.mono.pdf",
    ]
    for pattern in patterns:
        matches = sorted(output_dir.glob(pattern))
        if matches:
            return matches[-1]
    raise BabelDocError(f"Could not find BabelDOC output PDF in {output_dir}")


def _page_numbers_to_ranges(page_numbers: list[int]) -> str:
    if not page_numbers:
        raise BabelDocError("No pages requested for BabelDOC translation")
    pages = sorted({p + 1 for p in page_numbers})
    ranges: list[str] = []
    start = prev = pages[0]
    for page in pages[1:]:
        if page == prev + 1:
            prev = page
            continue
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = page
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(ranges)
