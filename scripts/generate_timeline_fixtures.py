"""Generate untracked timeline v1 fixtures for inspection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.timeline.fixtures import generate_fixture_set


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="directory for generated manifests and payloads")
    args = parser.parse_args()
    print(json.dumps(generate_fixture_set(args.output), indent=2))


if __name__ == "__main__":
    main()
