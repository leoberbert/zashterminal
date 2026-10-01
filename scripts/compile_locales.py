"""Compile checkout catalogs without installing or changing system files.

Usage: python3 scripts/compile_locales.py [pt es ...]
With no arguments, compile all languages present in locale/*.po.
"""

import argparse
import shutil
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("languages", nargs="*")
    args = parser.parse_args()
    compiler = shutil.which("msgfmt")
    if compiler is None:
        parser.error("msgfmt is required (GNU gettext).")
    directory = Path(__file__).resolve().parents[1] / "locale"
    available = {path.stem: path for path in directory.glob("*.po")}
    unknown = set(args.languages) - available.keys()
    if unknown:
        parser.error("Unknown languages: " + ", ".join(sorted(unknown)))
    for language in args.languages or sorted(available):
        target = directory / language / "LC_MESSAGES" / "zashterminal.mo"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".mo.tmp")
        try:
            subprocess.run(
                [compiler, "--check-format", "-o", str(temporary), str(available[language])],
                check=True,
            )
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        print(f"Compiled {language}: {target}")


if __name__ == "__main__":
    main()
