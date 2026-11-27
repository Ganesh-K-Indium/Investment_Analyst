"""
Check that the local OCR engines work on this machine, and download their models now rather than on the first upload.

    python -m app.docproc.engines.warmup                  every installed engine
    python -m app.docproc.engines.warmup rapidocr doctr   just these
    python -m app.docproc.engines.warmup --status         where each engine's models are, and whether they are downloaded yet (reads nothing)

For each engine it reads a small built-in page, then prints whether the page's values were found, how long it took, and the
first-use time (which includes loading, and downloading, the model). Exit status is 1 when a requested engine fails, so it can
gate a deployment. Run it on a connected machine to fill the model cache, then copy the cache to an air-gapped server (see
OCR engines in the README). Models are kept in DATA_DIR/docproc/models (DOCPROC_MODELS_DIR), inside the app's own data folder;
RapidOCR keeps its own inside its package folder.
"""
import asyncio
import sys
import time

from app.docproc.engines.ocr import local, sample


def status() -> int:
    """Installed or not, and whether the models are on disk yet. pip installs the code only: a neural engine downloads its
    models the first time it runs, so "already installed" says nothing about them."""
    for eng in local.engines():
        if not eng.available():
            print(f"  --    {eng.label:10} not installed. {eng.install}")
        elif eng.id == "tesseract":
            print(f"  ready {eng.label:10} installed (language data comes with Tesseract itself)")
        else:
            st = local.model_status(eng.id)
            if st["state"] == "bundled":
                print(f"  ready {eng.label:10} installed; its models are inside the package, nothing to download")
            elif st["state"] == "downloaded":
                print(f"  ready {eng.label:10} installed, models downloaded ({st['files']} files, {st['bytes'] / 1e6:.0f} MB) in {st['path']}")
            else:
                print(f"  TODO  {eng.label:10} installed, but the models are NOT downloaded yet. They download on first use, or now with:  python -m app.docproc.engines.warmup {eng.id}")
    return 0


async def check(eng: local.Engine) -> bool:
    jpeg = sample.page()
    start = time.perf_counter()
    first = await eng.read(jpeg)
    first_seconds = time.perf_counter() - start
    again = await eng.read(jpeg)
    score = sample.found(first.text)
    ok = score >= 0.75
    print(f"  {'OK    ' if ok else 'POOR  '}{eng.label:10} found {score:.0%} of the sample values; first page {first_seconds:.1f}s (includes loading the model), then {again.seconds:.1f}s")
    if not ok:
        print(f"         it read: {first.lines}")
    return ok


def main(argv: list) -> int:
    local.configure_caches()
    if "--status" in argv:
        return status()
    wanted = [a.lower() for a in argv]
    unknown = [a for a in wanted if local.get(a) is None]
    if unknown:
        print(f"Unknown engine: {', '.join(unknown)}. Choose from: {', '.join(e.id for e in local.engines())}")
        return 2
    engines = [local.get(a) for a in wanted] if wanted else local.engines()
    failed = False
    for eng in engines:
        if not eng.available():
            print(f"  --    {eng.label:10} not installed. {eng.install}")
            failed = failed or bool(wanted)          # asking for one that is missing is a failure; listing all is just a report
            continue
        try:
            failed = (not asyncio.run(check(eng))) or failed
        except Exception as e:
            print(f"  FAIL  {eng.label:10} {type(e).__name__}: {str(e)[:200]}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
