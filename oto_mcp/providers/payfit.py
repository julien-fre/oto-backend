"""Registry declaration of the `payfit` connector.

Single home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import _c

# payfit: payroll and HR, EVERYTHING the Partner API documents for READING. No
# write is wired (24/09/2026): write ops return
# `payfit_write_not_wired` (see `tools/payfit_garde.py`).
# keyed api_key (Bearer), BYO only: a PayFit API key is created by a
# company admin and only opens that company — a platform key
# would make no sense, and that is also why a GROUP of companies sets one connector
# instance per company (see `connectors/docs/payfit.md`).
#
# ⚠️ PAYROLL software: NIR, IBAN, pay, health-related absence reasons.
# Since 17/09/2026 (usage signal #1063) the connector no longer REMOVES
# anything hard-coded: everything is served, and protection goes through the **server
# default for field filters** (`field_filter_defaults.SERVER_DEFAULTS`), which
# the org_admin can lift — see `tools/payfit.py`.
#
# Three modules, a single key: the tools of `payfit.py` and its siblings, mounted
# together by `modules`.
CONNECTOR = _c(
    "payfit", ["payfit"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="PayFit",
    help="payroll and HR, read-only: company, directory, contracts, absences, "
         "payslips, payroll accounting, worked time, health insurance",
    href="https://payfit.com",
    modules=("payfit", "payfit_paie", "payfit_social"),
    # PayFit's word for an "account": one key = one COMPANY, and a group sets as many
    # as it has companies. Without this word, an agent stuck on an ambiguity
    # reads "several payfit accounts" and has to translate; with it, it reads "several
    # companies" and knows what it is looking for. Multi-account itself is not declared
    # here: it already applies to every connector whose credential can be set
    # (`Connector.auth_multi_account`).
    account_noun="company",
)

CATEGORY = "HR"
PUBLISHER = "PayFit"
LOGO_DOMAIN = "payfit.com"

DESCRIPTION = (
    "HR and financial management of a company run on PayFit: the employee "
    "directory, their contracts (nature, collective agreement, day-based "
    "package, probation, termination), their absences, payslips (metadata and PDF), "
    "payroll accounting entries and their export, the payment file, "
    "the payroll cycle status, actual worked time, meal vouchers, "
    "health insurance and provident fund. Read-only: the connector never writes "
    "to PayFit — create the key with read scopes. NIR, "
    "bank details and absence reason are "
    "masked by a server default that an org administrator can lift."
)
