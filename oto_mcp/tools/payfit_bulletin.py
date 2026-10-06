"""PayFit — the OVERTIME lines of a payslip, read from its text.

The PayFit API serves no payslip line: overtime is booked to the same account as
base salary in the accounting entries (641x), and worked time is only a monthly
total. The only place where it can be read is the payslip PDF. This module extracts the
lines that name it — and NOTHING else: the caller never receives the payslip text
(NIR, IBAN, address).

⚠️ **The payslip format is not a contract.** pypdf returns each row of the table
flat, columns glued together in page order; labels vary by agreement
(additional hours of a part-time contract, 10/25/50 % premiums, compensatory
rest). So the line is returned as it is read with its numbers in
order, without claiming to know which is the base, the rate or the amount: it is up to
the caller to check it against a payslip they have in front of them.
"""
from __future__ import annotations

import re
import unicodedata

# Labels of hours paid on top of the contract. Compared without accents or case.
_HEURES = re.compile(
    r"\bheures?\s+(?:supp?(?:lementaires?)?|compl(?:ementaires?)?|majorees?)\b"
    r"|\bh\.?\s?sup\b|\bhs\s?\d{2}\b|\bmajoration\s+\d{2}\s?%")
# Lines that NAME overtime without being its payment: contribution
# reduction, exemption, tax relief. Returned separately, never mixed.
_ALLEGEMENT = re.compile(r"\b(reduction|exoneration|deduction|defiscalis)")
# French number: thousands separated by a space (including non-breaking), decimal comma.
_NOMBRE = re.compile(r"-?\d{1,3}(?:[   ]\d{3})+(?:,\d+)?|-?\d+(?:[,.]\d+)?")

PAIEMENT = "paiement"
ALLEGEMENT = "allegement"


def _plat(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _nombre(tok: str) -> float:
    return float(re.sub(r"[   ]", "", tok).replace(",", "."))


def overtime_lines(text: str) -> list[dict]:
    """The lines of a payslip's text that name overtime / additional
    / premium-rate hours: `{kind, label, numbers, rates, line}`. `kind` = `paiement` or
    `allegement` (contribution reduction or exemption on those hours). `rates` =
    the line's percentages, removed from `numbers`."""
    out = []
    for brute in (text or "").splitlines():
        ligne = " ".join(brute.split())
        plat = _plat(ligne)
        if not ligne or not _HEURES.search(plat):
            continue
        rates = [_nombre(m) for m in re.findall(r"(\d+(?:[,.]\d+)?)\s?%", ligne)]
        sans_taux = re.sub(r"\d+(?:[,.]\d+)?\s?%", " ", ligne)
        m = _NOMBRE.search(sans_taux)
        label = (sans_taux[:m.start()] if m else sans_taux).strip(" :-")
        out.append({
            "kind": ALLEGEMENT if _ALLEGEMENT.search(plat) else PAIEMENT,
            "label": " ".join(label.split()),
            "numbers": [_nombre(t) for t in _NOMBRE.findall(sans_taux)],
            "rates": rates,
            "line": ligne,
        })
    return out
