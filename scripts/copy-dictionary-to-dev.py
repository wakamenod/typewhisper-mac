#!/usr/bin/env python3
"""Copy dictionary terms and corrections from the official app to TypeWhisper-Dev.

The official app and the dev build keep separate data. This reads the official
app's dictionary store directly, so it works even when the official app is not
running or has been removed, and registers the entries in the running dev build
through its HTTP API. Turn on the API server in TypeWhisper-Dev first.

Run with --dry-run to see what would be copied.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

SUPPORT = Path.home() / "Library" / "Application Support"
SOURCE_STORE = SUPPORT / "TypeWhisper" / "dictionary.store"
DEV_SUPPORT = SUPPORT / "TypeWhisper-Dev"
DEV_STORE = DEV_SUPPORT / "dictionary.store"
DEV_DISCOVERY = DEV_SUPPORT / "api-discovery.json"


def log(message: str) -> None:
    print(f"[typewhisper-dictionary-copy] {message}")


def read_entries(store: Path) -> list[dict]:
    """Return every dictionary entry in STORE, read without locking it."""
    connection = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "select ZENTRYTYPE, ZORIGINAL, ZREPLACEMENT, ZCASESENSITIVE, ZISENABLED,"
            " ZCTCMINSIMILARITY from ZDICTIONARYENTRY order by ZENTRYTYPE, ZORIGINAL"
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "type": entry_type,
            "original": original or "",
            "replacement": replacement or "",
            "case_sensitive": bool(case_sensitive),
            "enabled": bool(enabled),
            "ctc_min_similarity": ctc,
        }
        for entry_type, original, replacement, case_sensitive, enabled, ctc in rows
        if (original or "").strip()
    ]


def plan_copy(source: list[dict], dev: list[dict], include_disabled: bool, overwrite: bool) -> dict:
    """Decide which source entries to register in the dev build.

    TypeWhisper matches terms and correction originals ignoring case, so do the same
    when looking for entries the dev build already has.
    """
    dev_terms = {e["original"].casefold() for e in dev if e["type"] == "term"}
    dev_corrections = {e["original"].casefold(): e for e in dev if e["type"] == "correction"}
    plan = {"terms": [], "corrections": [], "present": [], "conflicts": [], "disabled": []}

    for entry in source:
        if not entry["enabled"] and not include_disabled:
            plan["disabled"].append(entry)
            continue
        key = entry["original"].casefold()
        if entry["type"] == "term":
            if key in dev_terms:
                plan["present"].append(entry)
            else:
                plan["terms"].append(entry)
                dev_terms.add(key)
        elif entry["type"] == "correction":
            existing = dev_corrections.get(key)
            if existing is None:
                plan["corrections"].append(entry)
            elif (existing["original"], existing["replacement"]) == (entry["original"], entry["replacement"]):
                plan["present"].append(entry)
            elif overwrite:
                plan["corrections"].append(entry)
            else:
                plan["conflicts"].append((entry, existing))
    return plan


def describe(entry: dict) -> str:
    if entry["type"] == "term":
        return f"term {entry['original']}"
    return f"correction {entry['original']} -> {entry['replacement']}"


class DevAPI:
    def __init__(self, discovery: Path) -> None:
        try:
            document = json.loads(discovery.read_text())
        except FileNotFoundError:
            sys.exit(f"error: {discovery} not found. Start TypeWhisper-Dev and turn on its API "
                     "server in Settings > Advanced.")
        self.base = f"http://127.0.0.1:{document['port']}"
        self.token = (document.get("token") or "").strip()

    def request(self, method: str, path: str, body: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(), headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise SystemExit(f"error: {method} {path} failed with HTTP {error.code}: {detail}")
        except urllib.error.URLError as error:
            raise SystemExit(f"error: cannot reach TypeWhisper-Dev at {self.base}: {error.reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="show the plan without copying")
    parser.add_argument("--include-disabled", action="store_true",
                        help="also copy disabled entries (they become enabled in the dev build)")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace dev corrections that map the same original differently")
    parser.add_argument("--source", type=Path, default=SOURCE_STORE, help="source dictionary store")
    args = parser.parse_args()

    if not args.source.exists():
        log(f"no dictionary at {args.source}; nothing to copy")
        return 0

    source = read_entries(args.source)
    dev = read_entries(DEV_STORE) if DEV_STORE.exists() else []
    plan = plan_copy(source, dev, args.include_disabled, args.overwrite)

    log(f"source: {len(source)} entries in {args.source}")
    log(f"to copy: {len(plan['terms'])} terms, {len(plan['corrections'])} corrections")
    log(f"already in dev: {len(plan['present'])}")
    for entry in plan["disabled"]:
        log(f"skip disabled {describe(entry)} (use --include-disabled to copy)")
    for entry, existing in plan["conflicts"]:
        log(f"skip {describe(entry)}: dev maps {existing['original']} -> "
            f"{existing['replacement']} (use --overwrite to replace)")

    if args.dry_run:
        for entry in plan["terms"] + plan["corrections"]:
            log(f"would copy {describe(entry)}")
        return 0
    if not plan["terms"] and not plan["corrections"]:
        log("nothing to copy")
        return 0

    api = DevAPI(DEV_DISCOVERY)
    if plan["terms"]:
        term_entries = []
        for entry in plan["terms"]:
            item = {"term": entry["original"]}
            if entry["ctc_min_similarity"] is not None:
                item["ctc_min_similarity"] = entry["ctc_min_similarity"]
            term_entries.append(item)
        api.request("PUT", "/v1/dictionary/terms", {"term_entries": term_entries, "replace": False})
        log(f"copied {len(plan['terms'])} terms")
    for entry in plan["corrections"]:
        api.request("PUT", "/v1/dictionary/corrections", {
            "original": entry["original"],
            "replacement": entry["replacement"],
            "caseSensitive": entry["case_sensitive"],
        })
    if plan["corrections"]:
        log(f"copied {len(plan['corrections'])} corrections")
    return 0


if __name__ == "__main__":
    sys.exit(main())
