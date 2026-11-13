"""The names no tracked file may carry (issue #100, owner 2026-11-11), shared by the repo-wide guard in
`test_repo_hygiene.py` and the case study's check in `test_docsite.py`.

Every name is spelled here in pieces, so this file does not hold one either.

- `VENDOR_PATTERN`: the vendor whose dry-end record the contract was modelled on, its product names, its record codes
  and its source-system table names. The contract describes a generic corrugator dry-end setup record. A short name
  must stand alone: a letter, digit, '+' or '/' before it, or a letter, '+' or '/' after it, is a base64 blob or
  another word; a digit or an underscore on either side is still the name (a numbered table, machine or knife, a
  snake_case column or slug). The long ones match anywhere. Every match ignores case (taken from #122, with the record
  codes added).
- `MAKER_PATTERN`: the other corrugator makers and plant-software vendors the case study's check listed (#120). A long
  name needs no letter or digit before it and no letter after it: a '+' or '/' beside it is still the name ("A/B", a
  URL path), as #120's word boundaries had it. The two three-letter names stand alone as the vendor's short names do,
  since a base64 blob could hold them between a '+' and a '/'. Every match ignores case.
- `PROSE_PATTERN`: #120's own check for a page of prose with no blob in it (the case study): every short vendor name and
  every maker between word boundaries, so a '+' or '/' on either side never shelters a name there.
"""
import re

SHORT_VENDOR_NAMES = ("B" + "HS", "P0" + "4[A-Z]", "H" + "QM", "A" + "SM", "K" + "QF")
LONG_VENDOR_NAMES = ("Production" + "_Center", "BQMES" + "PAHT", "Modul" + "e[ _-]?Facer", "Modul" + "[ _-]Facer", "K" + "QFSquare")
VENDOR_PATTERN = re.compile("(?<![A-Za-z0-9+/])(?:" + "|".join(SHORT_VENDOR_NAMES) + ")(?![A-Za-z+/])|" + "|".join(LONG_VENDOR_NAMES),
                            re.IGNORECASE)

SHORT_MAKER_NAMES = ("T" + "CY", "E" + "FI")
LONG_MAKER_NAMES = ("Fos" + "ber", "Mar" + "quip", "Ag" + "nati", "Mitsu" + "bishi", "Iso" + "wa", "Kiwi" + "plan", "Am" + "tech",
                    "Val" + "met", "Pet" + "ers", "Kol" + "bus", "Si" + "mon", "Lang" + "ston", "Van" + "guard")
MAKER_NAMES = SHORT_MAKER_NAMES + LONG_MAKER_NAMES
MAKER_PATTERN = re.compile("(?<![A-Za-z0-9+/])(?:" + "|".join(SHORT_MAKER_NAMES) + ")(?![A-Za-z+/])|"
                           "(?<![A-Za-z0-9])(?:" + "|".join(LONG_MAKER_NAMES) + ")(?![A-Za-z])", re.IGNORECASE)
PROSE_PATTERN = re.compile(r"\b(?:" + "|".join(SHORT_VENDOR_NAMES + MAKER_NAMES) + r")\b", re.IGNORECASE)
