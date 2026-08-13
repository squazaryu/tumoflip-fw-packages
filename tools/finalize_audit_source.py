#!/usr/bin/env python3
"""Bind one resolved Community Pack source to its canonical audit issue."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

try:
    from .audit_inputs import InputError, load_object, validate_source_fixture, write_json
except ImportError:  # Direct script execution.
    from audit_inputs import InputError, load_object, validate_source_fixture, write_json


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolved", type=Path, required=True)
    parser.add_argument("--issue-repository", required=True)
    parser.add_argument("--issue-number", type=int, required=True)
    parser.add_argument("--issue-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        value = load_object(args.resolved)
        if value.get("schema") != 1 or value.get("kind") != "protectedAuditResolvedSource":
            raise InputError("resolved source contract is invalid")
        if args.issue_repository != "squazaryu/tumoflip-fw-packages" or args.issue_number < 1:
            raise InputError("canonical issue identity is invalid")
        expected_url = f"https://github.com/{args.issue_repository}/issues/{args.issue_number}"
        if args.issue_url != expected_url or re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/[1-9][0-9]*", args.issue_url) is None:
            raise InputError("canonical issue URL differs")
        value["kind"] = "protectedAuditSourceFixture"
        value["canonicalIssue"] = {
            "repository": args.issue_repository,
            "number": args.issue_number,
            "url": args.issue_url,
        }
        validate_source_fixture(value)
        write_json(args.output, value)
    except (InputError, OSError, ValueError) as error:
        print(f"source fixture finalization failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
