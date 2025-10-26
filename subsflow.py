#!/usr/bin/env python3
"""
subsflow.py
-----------------------------------
End-to-end subtitle workflow tool:

Commands:
  1) clean   : Clean and deduplicate YouTube auto-generated SRT captions
  2) prep    : Convert SRT -> TSV (2- or 3-column) for LLM translation
  3) rejoin  : Reattach original timestamps to a translated TSV, with validation
  4) validate-tsv : Validate a TSV (columns, IDs) without rejoining

Highlights:
- "clean" removes rolling-window repetition while preserving timestamps (text only).
- "prep" writes TSV with escaped newlines so one caption = one line in TSV.
- "rejoin" checks TSV integrity (columns, IDs, counts) and offers to auto-fix extra tab columns.
- Accepts both 2-col TSV (id<TAB>translation) and 3-col TSV (id<TAB>original<TAB>translation).

Typical flows:

# 1) Clean
python3 subsflow.py clean original.srt original_clean.srt --numbering renumber

# 2) Prep (3-col recommended for robust alignment)
python3 subsflow.py prep original_clean.srt for_translation.tsv --format tsv --tsv-columns 3

# 3) Translate for_translation.tsv with your LLM to fill column 3

# 4) Rejoin (auto-fix TSV if needed)
python3 subsflow.py rejoin original_clean.srt French_translation.tsv final_French.srt --format tsv --align id --tsv-auto-fix --tsv-fixed-out French_fixed.tsv

# 5) Validate TSV alone (optional)
python3 subsflow.py validate-tsv French_translation.tsv --expect-columns 3
"""

from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path
from typing import List, Dict, Tuple, Optional


# ----------------------------
# Shared helpers and utilities
# ----------------------------

def normalize_space(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _escape_newlines_for_tsv(s: str) -> str:
    # Replace actual newlines with explicit \n so TSV stays one-line per caption
    return s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\n")


def _unescape_newlines_from_tsv(s: str) -> str:
    # Convert explicit \n back to actual newlines
    return s.replace(r"\n", "\n")


def prompt_yes_no(message: str, default: bool = False) -> bool:
    """
    Ask a yes/no question on TTY. If not a TTY (piped/cron), return default.
    """
    if not sys.stdin.isatty():
        return default
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        ans = input(message + suffix).strip().lower()
    except EOFError:
        return default
    if not ans:
        return default
    return ans in ("y", "yes")


# ----------------------------
# SRT parsing/writing
# ----------------------------

def parse_srt(path: str) -> List[Dict]:
    """
    Parse an .srt file into a list of entries:
      {
        'seq': int,               # 1-based index in file
        'id': Optional[int],      # numeric caption ID if present
        'start': str,
        'end': str,
        'text_lines': List[str],  # original text lines
        'text': str               # single-line normalized text
      }
    """
    raw = Path(path).read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return []

    blocks = re.split(r"(?:\r?\n){2,}", raw)

    entries: List[Dict] = []
    seq = 0
    for b in blocks:
        lines_raw = b.splitlines()
        lines = [ln.strip() for ln in lines_raw if ln.strip() != ""]
        if not lines:
            continue

        # Find timing line (contains -->)
        timing_idx = None
        for i, line in enumerate(lines[:4]):  # usually within first 2-3 lines
            if "-->" in line:
                timing_idx = i
                break
        if timing_idx is None:
            continue

        # Optional numeric ID before timing
        cap_id = None
        if timing_idx >= 1 and re.fullmatch(r"\d+", lines[0]):
            try:
                cap_id = int(lines[0])
            except ValueError:
                cap_id = None

        timing_line = lines[timing_idx]
        if "-->" not in timing_line:
            continue
        try:
            start, end = [x.strip() for x in timing_line.split("-->")]
        except Exception:
            continue

        text_lines = [ln for ln in lines[timing_idx + 1:]]
        text = normalize_space(" ".join(text_lines)) if text_lines else ""

        seq += 1
        entries.append({
            "seq": seq,
            "id": cap_id,
            "start": start,
            "end": end,
            "text_lines": text_lines,
            "text": text,
        })

    return entries


def write_srt(entries: List[Dict], path: str, numbering: str = "renumber") -> None:
    """
    Write entries back to .srt format.
    numbering:
      - "original": use original IDs if they exist, else fall back to sequential
      - "renumber": write 1..N (recommended for cleanliness)
    """
    with open(path, "w", encoding="utf-8") as out:
        for i, e in enumerate(entries, 1):
            if numbering == "renumber":
                idx = i
            else:
                idx = e["id"] if e.get("id") is not None else i
            out.write(f"{idx}\n")
            out.write(f"{e['start']} --> {e['end']}\n")
            out.write(f"{e['text']}\n\n")


# ------------------------------------
# CLEAN: YouTube auto-captions unroller
# ------------------------------------

def sanitize_word(tok: str) -> str:
    # Strip leading/trailing punctuation; keep alphanumerics and apostrophes
    return re.sub(r"^[^\w']+|[^\w']+$", "", tok).lower()


def normalize_text(s: str) -> str:
    # Collapse whitespace; keep tokens like ">>"
    return re.sub(r"\s+", " ", s).strip()


def longest_suffix_prefix_overlap(prev_text: str, curr_text: str) -> int:
    """
    Return number of tokens to drop from the start of curr_text because they
    match a suffix of prev_text (word-wise, punctuation-insensitive).
    """
    prev_norm = normalize_text(prev_text)
    curr_norm = normalize_text(curr_text)
    if not prev_norm or not curr_norm:
        return 0

    prev_raw = prev_norm.split()
    curr_raw = curr_norm.split()

    prev_san = [sanitize_word(t) for t in prev_raw if sanitize_word(t)]
    curr_san = []
    raw_idx_for_curr_san = []
    for i, t in enumerate(curr_raw):
        sw = sanitize_word(t)
        if sw:
            curr_san.append(sw)
            raw_idx_for_curr_san.append(i)

    if not prev_san or not curr_san:
        return 0

    max_k = min(len(prev_san), len(curr_san))
    for k in range(max_k, 0, -1):
        if prev_san[-k:] == curr_san[:k]:
            raw_drop_until = raw_idx_for_curr_san[k - 1]  # inclusive raw index
            return raw_drop_until + 1
    return 0


def cmd_clean(args) -> None:
    entries = parse_srt(args.input_srt)
    if not entries:
        raise SystemExit("No valid SRT entries found in input.")

    cleaned: List[Dict] = []
    prev_window_text = ""

    for e in entries:
        curr_text = normalize_text(e["text"])
        if not curr_text:
            prev_window_text = curr_text
            continue

        drop = longest_suffix_prefix_overlap(prev_window_text, curr_text)
        if drop > 0:
            raw_tokens = curr_text.split()
            curr_new = " ".join(raw_tokens[drop:]).strip()
        else:
            curr_new = curr_text

        if not curr_new:
            prev_window_text = curr_text
            continue

        if cleaned and normalize_text(cleaned[-1]["text"]) == curr_new:
            prev_window_text = curr_text
            continue

        new_e = dict(e)
        new_e["text"] = curr_new
        cleaned.append(new_e)
        prev_window_text = curr_text

    write_srt(cleaned, args.output_srt, numbering=args.numbering)
    print(f"OK: {len(entries)} → {len(cleaned)} captions written to {args.output_srt}")


# ------------------------------------------
# PREP: SRT -> translation-friendly TSV/blocks
# ------------------------------------------

def write_translation_prep(
    entries: List[Dict],
    out_path: str,
    fmt: str = "tsv",
    id_source: str = "original",
    join_with: str = "space",
    tsv_columns: int = 3
) -> None:
    """
    fmt: "blocks" or "tsv"
    id_source: "original" (use caption numbers if present, else seq)
               "seq"      (always use sequential order as ID)
    join_with: "space" (join lines by space) or "keep" (keep original line breaks)
    tsv_columns: 2 or 3
       - 2 columns: "<id>\\t<text>"
       - 3 columns: "<id>\\t<original_text>\\t" (3rd column left empty as skeleton)
    """
    def caption_id(e: Dict) -> int:
        if id_source == "seq":
            return e["seq"]
        return e["id"] if e.get("id") is not None else e["seq"]

    with open(out_path, "w", encoding="utf-8") as out:
        if fmt == "tsv":
            for e in entries:
                cid = caption_id(e)
                if join_with == "keep" and e["text_lines"]:
                    text = "\n".join(e["text_lines"]).strip()
                else:
                    text = e["text"]
                text_tsv = _escape_newlines_for_tsv(text)
                if tsv_columns == 3:
                    out.write(f"{cid}\t{text_tsv}\t\n")
                else:
                    out.write(f"{cid}\t{text_tsv}\n")
        else:
            # blocks format (no timestamps): ID line, then text, blank line between captions
            for e in entries:
                cid = caption_id(e)
                if join_with == "keep" and e["text_lines"]:
                    text_block = "\n".join(e["text_lines"]).strip()
                else:
                    text_block = e["text"]
                out.write(f"{cid}\n{text_block}\n\n")


def cmd_prep(args) -> None:
    entries = parse_srt(args.input_srt)
    if not entries:
        raise SystemExit("No valid SRT entries found.")

    write_translation_prep(
        entries,
        args.output_txt,
        fmt=args.format,
        id_source=args.id_source,
        join_with=args.join_lines,
        tsv_columns=args.tsv_columns
    )
    if args.format == "tsv":
        print(f"OK: wrote {len(entries)} TSV captions to {args.output_txt} "
              f"({args.tsv_columns} columns; IDs={args.id_source}, join={args.join_lines})")
    else:
        print(f"OK: wrote {len(entries)} blocks to {args.output_txt} (IDs={args.id_source}, join={args.join_lines})")


# ----------------------------------------------------
# TSV parsing/validation/fixing and REJOIN to SRT
# ----------------------------------------------------

class TSVReport:
    def __init__(self):
        self.total_lines = 0
        self.field_histogram: Dict[int, int] = {}
        self.bad_id_lines: List[int] = []
        self.rows: List[List[str]] = []  # raw split by tabs

    def record_line(self, nf: int):
        self.field_histogram[nf] = self.field_histogram.get(nf, 0) + 1


def read_tsv_with_report(path: str) -> TSVReport:
    rep = TSVReport()
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    for ln_no, line in enumerate(lines, 1):
        if not line.strip():
            # preserve empties as no fields if needed; we skip them here
            continue
        parts = line.split("\t")
        rep.rows.append(parts)
        rep.total_lines += 1
        rep.record_line(len(parts))
        if not parts or not parts[0].strip().isdigit():
            rep.bad_id_lines.append(ln_no)
    return rep


def summarize_report(rep: TSVReport) -> str:
    parts = [f"Total non-empty lines: {rep.total_lines}"]
    if rep.field_histogram:
        hist = " ".join(f"{v}x{nf}" for nf, v in sorted(rep.field_histogram.items()))
        parts.append(f"Columns per line histogram: {hist} (format: count x fields)")
    if rep.bad_id_lines:
        parts.append(f"Lines with non-numeric IDs: {len(rep.bad_id_lines)} (e.g., {rep.bad_id_lines[:10]})")
    return "\n".join(parts)


def fix_tsv_rows_merge_middle_columns(rows: List[List[str]]) -> List[List[str]]:
    """
    Normalize rows to exactly 3 columns: [id, english, french]
    - If len(row) == 2: treat as [id, translation]; produce [id, "", translation]
    - If len(row) >= 3: id, merge middle cols into english, last is translation
    - Trim surrounding whitespace in columns
    """
    fixed: List[List[str]] = []
    for parts in rows:
        if len(parts) < 2:
            # Skip malformed rows that have fewer than 2 fields
            continue
        cid = parts[0].strip()
        if len(parts) == 2:
            english = ""
            french = parts[1].strip()
        else:
            english = "\t".join(p.strip() for p in parts[1:-1]).strip()
            french = parts[-1].strip()
        # Normalize internal whitespace (but do not unescape \n here)
        english = re.sub(r"\s+", " ", english)
        french = re.sub(r"\s+", " ", french)
        fixed.append([cid, english, french])
    return fixed


def parse_translated_pairs_from_rows(rows: List[List[str]], translation_col: str = "auto") -> List[Tuple[int, str]]:
    """
    Given split rows (tab-split), extract (id, translated_text).
    Accepts:
      - 2-col: [id, translation]
      - 3-col: [id, original, translation]
      - N-col (N>3): will read the last column as translation if translation_col is 'auto'/'last'
    """
    out: List[Tuple[int, str]] = []
    for parts in rows:
        if len(parts) < 2:
            continue
        id_str = parts[0].strip()
        if not re.fullmatch(r"\d+", id_str):
            continue
        cid = int(id_str)

        col_choice = translation_col.lower()
        if col_choice in ("auto", "last"):
            idx = len(parts) - 1
        elif col_choice == "2":
            if len(parts) < 2:
                raise ValueError("Row lacks 2nd column for translation.")
            idx = 1
        elif col_choice == "3":
            if len(parts) < 3:
                raise ValueError("Row lacks 3rd column for translation.")
            idx = 2
        else:
            raise ValueError("translation_col must be one of: auto, last, 2, 3")

        text = parts[idx]
        text = _unescape_newlines_from_tsv(text)
        text_norm = normalize_space(text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " "))
        out.append((cid, text_norm))
    return out


def rejoin_translations(
    original_entries: List[Dict],
    translated_pairs: List[Tuple[int, str]],
    align: str = "id",
    strict: bool = True
) -> List[Dict]:
    """
    Merge translated text with original timestamps.
    align: 'id' (preferred) or 'seq'
    strict: enforce equal counts and ID sets (when align='id')
    """
    if align not in {"id", "seq"}:
        raise ValueError("align must be 'id' or 'seq'")

    n_orig = len(original_entries)
    n_trans = len(translated_pairs)

    if align == "seq":
        n = min(n_orig, n_trans)
        merged = []
        for i in range(n):
            e = dict(original_entries[i])
            e["text"] = translated_pairs[i][1]
            merged.append(e)
        if strict and n != n_orig:
            raise ValueError(f"Translated entries fewer than original (got {n_trans}, need {n_orig}).")
        return merged

    # align by id
    orig_by_id: Dict[int, Dict] = {}
    for e in original_entries:
        if e.get("id") is None:
            raise ValueError("Original SRT lacks caption numbers for some entries; cannot align by id.")
        oid = int(e["id"])
        orig_by_id[oid] = e

    trans_by_id: Dict[int, str] = {cid: txt for cid, txt in translated_pairs}

    if strict:
        missing_in_trans = [oid for oid in orig_by_id.keys() if oid not in trans_by_id]
        extra_in_trans = [tid for tid in trans_by_id.keys() if tid not in orig_by_id]
        if missing_in_trans or extra_in_trans:
            msg = []
            if missing_in_trans:
                msg.append(f"Missing translated IDs (first 10): {missing_in_trans[:10]}")
            if extra_in_trans:
                msg.append(f"Unexpected translated IDs (first 10): {extra_in_trans[:10]}")
            raise ValueError("ID mismatch in strict id mode. " + " ".join(msg))
        if len(trans_by_id) != len(orig_by_id):
            raise ValueError(f"Count mismatch (orig={len(orig_by_id)}, translated={len(trans_by_id)}) in strict id mode.")

    merged: List[Dict] = []
    for e in original_entries:
        oid = int(e["id"]) if e.get("id") is not None else None
        if oid is not None and oid in trans_by_id:
            txt = trans_by_id[oid]
        else:
            if strict:
                raise ValueError(f"Missing translation for caption id {oid}")
            txt = ""
        new_e = dict(e)
        new_e["text"] = txt
        merged.append(new_e)
    return merged


def validate_tsv_against_srt(tsv_rows: List[List[str]], srt_entries: List[Dict], align: str = "id") -> Tuple[bool, str]:
    """
    Basic integrity checks: numeric IDs, counts, and ID set match (if align=id).
    Returns (ok, message).
    """
    # Filter out lines with non-numeric IDs
    id_rows = [(int(r[0]), r) for r in tsv_rows if r and r[0].strip().isdigit()]
    if len(id_rows) != len(tsv_rows):
        return False, f"{len(tsv_rows) - len(id_rows)} line(s) have non-numeric IDs."

    if align == "seq":
        if len(id_rows) != len(srt_entries):
            return False, f"Count mismatch (TSV={len(id_rows)}, SRT={len(srt_entries)})."
        return True, "TSV looks consistent for seq alignment."

    # align by id
    srt_ids = [int(e["id"]) for e in srt_entries if e.get("id") is not None]
    if len(srt_ids) != len(srt_entries):
        return False, "Original SRT missing numeric IDs; cannot align by id."

    tsv_ids = [cid for cid, _ in id_rows]
    if len(tsv_ids) != len(srt_ids):
        return False, f"Count mismatch (TSV={len(tsv_ids)}, SRT={len(srt_ids)})."

    if set(tsv_ids) != set(srt_ids):
        # Identify a small diff sample
        missing = list(sorted(set(srt_ids) - set(tsv_ids)))[:10]
        extra = list(sorted(set(tsv_ids) - set(srt_ids)))[:10]
        return False, f"ID set differs. Missing in TSV (sample): {missing}; Extra in TSV (sample): {extra}"

    return True, "TSV IDs match SRT IDs."


def cmd_rejoin(args) -> None:
    # Load source SRT (timestamps)
    original_entries = parse_srt(args.original_srt)
    if not original_entries:
        raise SystemExit("No valid SRT entries found in original.")

    # Read TSV with report
    rep = read_tsv_with_report(args.translated_txt)
    print("TSV preflight:")
    print(summarize_report(rep))

    # If any row has fields > 3 or < 2, it’s malformed for our purposes.
    malformed = any((nf < 2 or nf > 3) for nf in rep.field_histogram)
    if malformed:
        print("Detected TSV rows with unexpected number of columns (not 2 or 3).")
        do_fix = args.tsv_auto_fix or prompt_yes_no("Attempt to auto-fix by collapsing extra tabs into column 2?", default=True)
        if not do_fix:
            raise SystemExit("Aborting. Please fix TSV and retry (or use --tsv-auto-fix).")

        fixed_rows = fix_tsv_rows_merge_middle_columns(rep.rows)
        if args.tsv_fixed_out:
            Path(args.tsv_fixed_out).write_text(
                "\n".join("\t".join(r) for r in fixed_rows) + "\n", encoding="utf-8"
            )
            print(f"Saved fixed TSV to: {args.tsv_fixed_out}")
        # Overwrite in-memory rows to proceed
        rep.rows = fixed_rows
        # Update report summary after fix
        post_hist = {3: len(fixed_rows)}
        print(f"Post-fix: {len(fixed_rows)} lines normalized to 3 columns.")
    else:
        # If exactly 2 columns uniformly, we will treat col 2 as translation directly.
        pass

    # Validate TSV against SRT metadata (counts and/or IDs)
    ok, msg = validate_tsv_against_srt(rep.rows, original_entries, align=args.align)
    print("TSV vs SRT check:", msg)
    if not ok and args.strict:
        raise SystemExit("TSV validation failed (strict). Use --no-strict or fix input.")
    elif not ok:
        print("Proceeding despite validation warnings (strict disabled).")

    # Convert to (id, text) pairs
    pairs = parse_translated_pairs_from_rows(rep.rows, translation_col=args.tsv_translation_col)

    # Merge with timestamps
    merged = rejoin_translations(
        original_entries,
        pairs,
        align=args.align,
        strict=args.strict
    )
    write_srt(merged, args.output_srt, numbering=args.numbering)
    print(f"OK: rejoined {len(merged)} captions -> {args.output_srt} "
          f"(format=tsv, align={args.align}, numbering={args.numbering})")


def cmd_validate_tsv(args) -> None:
    rep = read_tsv_with_report(args.tsv_file)
    print(summarize_report(rep))
    # Expect N columns?
    if args.expect_columns:
        bad = [i for i, row in enumerate(rep.rows, 1) if len(row) != args.expect_columns]
        if bad:
            print(f"Lines with != {args.expect_columns} columns: {len(bad)} (e.g., {bad[:10]})")
        else:
            print(f"All lines have {args.expect_columns} columns.")
    # Check numeric IDs
    if rep.bad_id_lines:
        print(f"Lines with non-numeric IDs: {len(rep.bad_id_lines)} (e.g., {rep.bad_id_lines[:10]})")
    else:
        print("All IDs are numeric.")


# ----------------------------
# CLI
# ----------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Subtitle pipeline: clean SRT, prep SRT->TSV, and rejoin TSV->SRT with validation."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # clean
    pclean = sub.add_parser("clean", help="Clean/deduplicate YouTube auto-generated SRT.")
    pclean.add_argument("input_srt", help="Path to input .srt (auto-generated).")
    pclean.add_argument("output_srt", help="Path to write cleaned .srt.")
    pclean.add_argument("--numbering", choices=["original", "renumber"], default="renumber",
                        help="Numbering policy for output .srt (default: renumber).")
    pclean.set_defaults(func=cmd_clean)

    # prep
    pprep = sub.add_parser("prep", help="Strip timestamps; keep IDs and text for translation.")
    pprep.add_argument("input_srt", help="Path to source .srt (usually cleaned).")
    pprep.add_argument("output_txt", help="Path to write translation-friendly file (no timestamps).")
    pprep.add_argument("--format", choices=["blocks", "tsv"], default="tsv",
                       help="Output format for translation file (default: tsv).")
    pprep.add_argument("--id-source", choices=["original", "seq"], default="original",
                       help="Which ID to write (default: original).")
    pprep.add_argument("--join-lines", choices=["space", "keep"], default="space",
                       help="Join original caption lines by spaces or keep line breaks (default: space).")
    pprep.add_argument("--tsv-columns", type=int, choices=[2, 3], default=3,
                       help="TSV columns for prep output (2 or 3; default: 3).")
    pprep.set_defaults(func=cmd_prep)

    # rejoin
    prej = sub.add_parser("rejoin", help="Reattach original timestamps to translated TSV.")
    prej.add_argument("original_srt", help="Path to source .srt (timestamps come from here).")
    prej.add_argument("translated_txt", help="Path to translated TSV (2 or 3 columns).")
    prej.add_argument("output_srt", help="Path to write final .srt with timestamps.")
    prej.add_argument("--format", choices=["tsv", "blocks"], default="tsv",
                      help="Format of the translated file (default: tsv).")
    prej.add_argument("--align", choices=["id", "seq"], default="id",
                      help="Align translations to originals by 'id' or by sequence order (default: id).")
    try:
        # Python 3.9+: BooleanOptionalAction
        prej.add_argument("--strict", action=argparse.BooleanOptionalAction, default=True,
                          help="Require counts/IDs to match; disable for best-effort.")
    except AttributeError:
        prej.add_argument("--strict", dest="strict", action="store_true",
                          help="Require counts/IDs to match; enable for strict.")
        prej.add_argument("--no-strict", dest="strict", action="store_false",
                          help="Disable strict checks for best-effort.")
        prej.set_defaults(strict=True)
    prej.add_argument("--numbering", choices=["original", "renumber"], default="original",
                      help="Numbering policy for output .srt (default: original).")
    prej.add_argument("--tsv-translation-col", choices=["auto", "last", "2", "3"], default="auto",
                      help="For TSV input: which column contains the translation (default: auto/last).")
    prej.add_argument("--tsv-auto-fix", action="store_true",
                      help="Auto-fix TSV with extra tabs by collapsing middle columns into column 2.")
    prej.add_argument("--tsv-fixed-out", default=None,
                      help="If fixing is applied, save a corrected TSV copy here.")
    prej.set_defaults(func=cmd_rejoin)

    # validate-tsv
    pv = sub.add_parser("validate-tsv", help="Validate TSV structure (columns, numeric IDs).")
    pv.add_argument("tsv_file", help="Path to TSV to validate.")
    pv.add_argument("--expect-columns", type=int, default=3,
                    help="Expected number of columns per line (default: 3).")
    pv.set_defaults(func=cmd_validate_tsv)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Dispatch
    if not hasattr(args, "func"):
        parser.print_help(sys.stderr)
        raise SystemExit(2)
    args.func(args)


if __name__ == "__main__":
    main()