"""python -m novaops.workflows.vendor SOURCE.md [--source-type ...]"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from novaops.core.config import Settings
from novaops.workflows.vendor.extractor import SOURCE_TYPES
from novaops.workflows.vendor.service import run_vendor_file


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m novaops.workflows.vendor")
    parser.add_argument("source", type=Path, help="Vendor source document (markdown/text)")
    parser.add_argument("--source-type", choices=sorted(set(SOURCE_TYPES.values())))
    parser.add_argument("--source-id")
    args = parser.parse_args()
    settings = Settings.from_env()
    options = {"source_type": args.source_type}
    if args.source_id:
        options["source_id"] = args.source_id
    result = run_vendor_file(settings, args.source, **options)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
