"""Run a waterfall LOCALLY against the real providers — no server, no database, no Logto.

The real tools are mounted on a bare FastMCP (`register_all`) and called for real;
only what needs the platform is replaced:

- keys come from the environment: `OTO_KEY_<PROVIDER>` (e.g. `OTO_KEY_HUNTER`,
  `OTO_KEY_APOLLO`, `OTO_KEY_AIARK`, `OTO_KEY_LEMLIST`), or `OTO_FIELDS_<PROVIDER>` as
  JSON for a multi-field card (`OTO_FIELDS_FORAGER='{"api_key": "…", "account_id": "…"}'`).
  A provider without one counts as "no key" — skipped without being called;
- the `steps` setting comes from `OTO_WATERFALL_STEPS` (e.g. `lemlist,hunter,apollo`);
- `meta.executer_cible` runs the tool directly: NO activation gate, call axes or
  `tool_calls` row (those need the database) — the provider call itself is real and
  BILLED on your key.

Usage (from the repo root, with the repo's venv):

    OTO_KEY_HUNTER=… OTO_KEY_APOLLO=… .venv/bin/python scripts/waterfall_local.py \\
        email --first_name Jane --last_name Doe --domain acme.com [--dry_run] \\
        [--providers hunter,apollo] [--raw]

    waterfall: email | phone | company | person | tech. Inputs: --linkedin_url --email
    --first_name --last_name --full_name --domain --company_name --siren --country
    --technologies (tech: comma-separated, e.g. `Shopify,HubSpot`).

    tech: `OTO_FIELDS_DATAFORSEO='{"login": "…", "password": "…"}'`, `OTO_KEY_APIFY`,
    `OTO_KEY_THEIRSTACK`.
    --raw also prints what each step's tool returned (to check an extractor).

Start with `--dry_run`: it spends nothing and shows the order and the arguments.

The French registry steps (`fr_get`, `fr_search`) go through the internal open-data
service (ADR 0028): locally they fail unless `FOD_BASE_URL` / `FOD_API_TOKEN` are set,
and the cascade moves on to the next step.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from types import SimpleNamespace


def _env_fields(provider: str):
    raw = os.environ.get(f"OTO_FIELDS_{provider.upper()}")
    if raw:
        return json.loads(raw)
    key = os.environ.get(f"OTO_KEY_{provider.upper()}")
    return {"key": key, "api_key": key} if key else None


def _install(raw_log: list):
    from mcp.types import ErrorData, INVALID_PARAMS
    from oto_mcp import access, error_taxonomy, providers
    from oto_mcp.mcp_errors import McpError
    from oto_mcp.tools import meta, waterfall

    def _missing(provider):
        return McpError(ErrorData(code=INVALID_PARAMS, message=(
            f"No `{provider}` key in the environment (OTO_KEY_{provider.upper()}).")))

    def resolve_api_key(provider, *a, **k):
        f = _env_fields(provider)
        if not f:
            raise _missing(provider)
        return f.get("key") or f.get("api_key"), False

    def resolve_credential(provider, *a, **k):
        if provider.startswith("waterfall_"):
            steps = os.environ.get("OTO_WATERFALL_STEPS")
            if not steps:
                raise _missing(provider)
            return SimpleNamespace(fields={"steps": steps}, key=None, is_platform=False,
                                   config={}, mode="user")
        f = _env_fields(provider)
        if not f:
            raise _missing(provider)
        return SimpleNamespace(key=f.get("key") or f.get("api_key"), fields=f,
                               is_platform=False, config={}, mode="user")

    def key_modes(sub, tools):
        out = {}
        for t in tools:
            carrier = waterfall._carrier(t)
            if carrier is None or carrier not in providers.CREDENTIAL_PROVIDERS:
                out[t] = "open"
            elif carrier == "sirene":
                out[t] = "platform"      # open data: the registry steps need no key
            else:
                out[t] = "user" if _env_fields(carrier) else "forbidden"
        return out

    async def executer_cible(tool, sub, name, demande, args):
        started = time.monotonic()
        try:
            result = await tool.run(args)
        except Exception as e:  # noqa: BLE001 — the step's failure is a result, as in prod
            info = error_taxonomy.classify(e)
            raw_log.append((name, args, f"ERROR {info.code}: {info.message}"))
            return meta.IssueCible(ok=False, message=info.message, code=info.code)
        issue = meta.IssueCible(ok=True, result=result)
        raw_log.append((name, args, waterfall._payload(issue),
                        int((time.monotonic() - started) * 1000)))
        return issue

    access.resolve_api_key = resolve_api_key
    access.resolve_credential = resolve_credential
    access.record_platform_usage = lambda *a, **k: None
    waterfall._key_modes = key_modes
    waterfall.current_user_sub_from_token = lambda: None
    meta.executer_cible = executer_cible


def main() -> int:
    p = argparse.ArgumentParser(description="Run a waterfall locally (real providers).")
    p.add_argument("waterfall", choices=["email", "phone", "company", "person", "tech"])
    for name in ("linkedin_url", "email", "first_name", "last_name", "full_name",
                 "domain", "company_name", "siren", "country", "technologies", "providers"):
        p.add_argument(f"--{name}")
    p.add_argument("--dry_run", action="store_true")
    p.add_argument("--raw", action="store_true")
    a = p.parse_args()

    from fastmcp import FastMCP
    from oto_mcp.tools import register_all

    raw_log: list = []
    _install(raw_log)
    mcp = FastMCP("local")
    register_all(mcp)
    tool = asyncio.run(mcp.get_tool(f"waterfall_{a.waterfall}"))
    accepted = set(tool.parameters["properties"])
    kwargs = {k: v for k, v in vars(a).items()
              if v not in (None, False) and k in accepted and k != "waterfall"}
    if "providers" in kwargs:
        kwargs["providers"] = [x for x in kwargs["providers"].split(",") if x]
    ignored = [k for k, v in vars(a).items()
               if v not in (None, False) and k not in accepted
               and k not in ("waterfall", "raw")]
    if ignored:
        print(f"(ignored for {a.waterfall}: {', '.join(ignored)})", file=sys.stderr)

    out = asyncio.run(tool.fn(**kwargs))
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    if a.raw:
        for entry in raw_log:
            print(f"\n--- {entry[0]} {json.dumps(entry[1], ensure_ascii=False)}",
                  file=sys.stderr)
            print(json.dumps(entry[2], indent=2, ensure_ascii=False, default=str)[:6000],
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
