"""Smoke LIVE du connecteur Airtable — le tool layer réel + le client réel + un vrai
Personal Access Token.

Même astuce que les tests unitaires (register sur un FastMCP nu, appel du `fn` du
tool), mais SANS mock : `resolve_api_key` est remplacé par le token lu dans l'env,
l'appel part vraiment chez Airtable.

Par défaut, LECTURE SEULE : whoami -> bases accordées -> schéma d'une base -> champs
d'une table -> lignes (page de 5) -> commentaires de la première ligne -> la forme
POST de la liste (échappatoire aux formules trop longues). Rien n'écrit tant que
`AIRTABLE_SMOKE_WRITE=1` n'est pas posé.

Avec WRITE=1, sur la table désignée par `AIRTABLE_TEST_TABLE` :
création d'une ligne -> update PATCH -> upsert sur le champ primaire -> commentaire
créé/édité/supprimé -> pièce jointe uploadée SI une colonne attachment est fournie
(`AIRTABLE_TEST_ATTACHMENT_FIELD`) -> **les lignes créées sont supprimées**. Net-zéro
sur les lignes.

⚠️ Les écritures de SCHÉMA (créer une table, créer un champ, renommer) ne sont
exercées que si `AIRTABLE_SMOKE_SCHEMA=1`, et elles sont IRRÉVERSIBLES PAR L'API :
Airtable n'expose ni suppression de table, ni suppression de champ, ni suppression de
base. Ce que ce script crée sous SCHEMA=1 doit être nettoyé À LA MAIN dans
l'interface. À ne poser que sur une base jetable.

Ce script est aussi le juge de trois points que la doc SPA d'Airtable ne rend pas
lisiblement, et qui sont marqués ⚠️ dans le client :
  1. `POST /{base}/{table}/listRecords` existe-t-il toujours ?
  2. `PATCH /meta/bases/{base}/tables/{table}` et `…/fields/{field}` (seuls les POST
     sont confirmés en doc) — exercés sous SCHEMA=1 ;
  3. l'hôte `content.airtable.com` pour `uploadAttachment`.
Chacun imprime un verdict explicite.

Lancer :  AIRTABLE_API_KEY=pat… \
          [AIRTABLE_TEST_BASE=app… [AIRTABLE_TEST_TABLE=tbl…
          [AIRTABLE_TEST_ATTACHMENT_FIELD="Pièces jointes"]
          [AIRTABLE_SMOKE_WRITE=1] [AIRTABLE_SMOKE_SCHEMA=1]]] \
          OTO_CONFIG_DISABLE_SOPS=1 .venv/bin/python -m scripts.airtable_smoke_test

Le token n'est JAMAIS imprimé.
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
from unittest.mock import patch

from fastmcp import FastMCP

_VERDICTS: list[str] = []


def _tool(m, name):
    return asyncio.run(m.get_tool(name)).fn


def _verdict(question: str, ok: bool, detail: str = "") -> None:
    mark = "✓" if ok else "✗"
    _VERDICTS.append(f"  {mark} {question}" + (f" — {detail}" if detail else ""))


def main() -> int:  # noqa: C901 — un smoke est une suite linéaire, pas une abstraction
    token = os.environ.get("AIRTABLE_API_KEY")
    if not token:
        print("✗ AIRTABLE_API_KEY absent de l'env (PAT `pat…`)")
        return 2
    write = os.environ.get("AIRTABLE_SMOKE_WRITE") == "1"
    schema_write = os.environ.get("AIRTABLE_SMOKE_SCHEMA") == "1"

    from oto_mcp.tools import airtable

    m = FastMCP("smoke-airtable")
    airtable.register(m)
    base_tool = _tool(m, "airtable_base")
    table_tool = _tool(m, "airtable_table")
    field_tool = _tool(m, "airtable_field")
    record_tool = _tool(m, "airtable_record")
    comment_tool = _tool(m, "airtable_comment")
    attachment_tool = _tool(m, "airtable_attachment")

    with patch("oto_mcp.access.resolve_api_key", return_value=(token, False)):
        # ---------------------------------------------------------- identité
        print("→ airtable_base(op='whoami')")
        me = base_tool(op="whoami")
        print(f"  ✓ user={me.get('id')} email={me.get('email') or '(scope absent)'} "
              f"scopes={me.get('scopes') or '(PAT : non rendus)'}")

        print("→ airtable_base(op='list')")
        bases = base_tool()
        print(f"  ✓ {bases['count']} base(s) accordée(s)")
        for b in bases["bases"][:10]:
            print(f"   - {b.get('name')!r} id={b.get('id')} "
                  f"perm={b.get('permissionLevel')}")
        if not bases["bases"]:
            print("  ✗ aucune base accordée au token — rien à sonder plus loin.")
            print(f"    {bases.get('hint')}")
            return 1

        base_id = os.environ.get("AIRTABLE_TEST_BASE") or bases["bases"][0]["id"]
        print(f"  (base sondée : {base_id})")

        # ------------------------------------------------------------ schéma
        print(f"→ airtable_table(base_id={base_id!r})")
        schema = table_tool(base_id=base_id)
        print(f"  ✓ {schema['count']} table(s)")
        for t in schema["tables"][:10]:
            print(f"   - {t.get('name')!r} id={t.get('id')} "
                  f"{len(t.get('fields') or [])} champ(s)")
        if not schema["tables"]:
            print("  ✗ base vide — rien à sonder plus loin.")
            return 1

        table_id = os.environ.get("AIRTABLE_TEST_TABLE") or schema["tables"][0]["id"]
        print(f"  (table sondée : {table_id})")

        print(f"→ airtable_field(base_id={base_id!r}, table_id={table_id!r})")
        fields = field_tool(base_id=base_id, table_id=table_id)
        primary = fields["table"]["primaryFieldId"]
        primary_name = next(
            (f["name"] for f in fields["fields"] if f["id"] == primary), None)
        print(f"  ✓ {fields['count']} champ(s), primaire={primary_name!r}")
        for f in fields["fields"][:15]:
            print(f"   - {f.get('name')!r} ({f.get('type')}) id={f.get('id')}")

        # ------------------------------------------------------------ lignes
        print(f"→ airtable_record(base_id={base_id!r}, table={table_id!r}, "
              "max_records=5)")
        page = record_tool(base_id=base_id, table=table_id, max_records=5)
        print(f"  ✓ {page['count']} ligne(s) rendue(s)"
              + (f", reste des pages (offset={page['offset']})" if page.get("more")
                 else ", table entièrement lue"))
        for r in page["records"][:5]:
            print(f"   - {r.get('id')} {list((r.get('fields') or {}).items())[:3]}")

        if page["records"]:
            sample = page["records"][0]["id"]
            print(f"→ airtable_record(op='get', record_id={sample!r})")
            one = record_tool(base_id=base_id, table=table_id, op="get",
                              record_id=sample)
            assert one["id"] == sample
            print("  ✓ get-by-id cohérent avec list")

            print(f"→ airtable_comment(record_id={sample!r})")
            comments = comment_tool(base_id=base_id, table=table_id,
                                    record_id=sample, max_comments=5)
            print(f"  ✓ {comments['count']} commentaire(s)")

        # ⚠️ point 1 : la forme POST de la liste existe-t-elle encore ?
        print("→ POST …/listRecords (formule longue) — vérification de l'endpoint")
        long_formula = ("OR(" + ",".join(
            f"{{{primary_name}}}='sonde-{i}'" for i in range(700)) + ")")
        try:
            assert len(long_formula) > airtable._FORMULA_URL_LIMIT, "formule trop courte"
            post_page = record_tool(base_id=base_id, table=table_id,
                                    filter_by_formula=long_formula, max_records=1)
            _verdict("POST /listRecords existe", True,
                     f"{post_page['count']} ligne(s) (0 attendu)")
        except Exception as e:  # noqa: BLE001 — c'est le verdict qu'on veut, pas la trace
            _verdict("POST /listRecords existe", False, str(e)[:180])

        if not write:
            print("\n(lecture seule — poser AIRTABLE_SMOKE_WRITE=1 pour les écritures)")
            print("\nVerdicts :")
            print("\n".join(_VERDICTS))
            return 0

        # ============================================================ ÉCRITURES
        created: list[str] = []
        stamp = os.environ.get("AIRTABLE_SMOKE_STAMP", "oto-smoke")
        try:
            print(f"\n→ airtable_record(op='create') — 2 lignes {stamp!r}")
            receipt = record_tool(
                base_id=base_id, table=table_id, op="create",
                records=[{primary_name: f"{stamp}-1"}, {primary_name: f"{stamp}-2"}])
            created = [r["id"] for r in receipt["records"]]
            print(f"  ✓ {receipt['succeeded']}/{receipt['total']} créée(s) : {created}")

            print("→ airtable_record(op='update') — PATCH sur la 1re")
            record_tool(base_id=base_id, table=table_id, op="update",
                        record_id=created[0], fields={primary_name: f"{stamp}-1-maj"})
            after = record_tool(base_id=base_id, table=table_id, op="get",
                                record_id=created[0])
            assert after["fields"][primary_name] == f"{stamp}-1-maj"
            print("  ✓ PATCH effectif et non destructif")

            print(f"→ airtable_record(op='upsert', merge_on=[{primary_name!r}])")
            up = record_tool(
                base_id=base_id, table=table_id, op="upsert",
                merge_on=[primary_name],
                records=[{primary_name: f"{stamp}-2"},
                         {primary_name: f"{stamp}-3-neuve"}])
            new_ids = [r["id"] for r in up["records"] if r["id"] not in created]
            created += new_ids
            print(f"  ✓ upsert : {up['succeeded']} ligne(s) touchée(s), "
                  f"{len(new_ids)} créée(s)")

            print("→ airtable_comment : create → update → delete")
            c = comment_tool(base_id=base_id, table=table_id, record_id=created[0],
                             op="create", text=f"{stamp} : sonde")
            comment_tool(base_id=base_id, table=table_id, record_id=created[0],
                         op="update", comment_id=c["id"], text=f"{stamp} : sonde (maj)")
            comment_tool(base_id=base_id, table=table_id, record_id=created[0],
                         op="delete", comment_id=c["id"])
            print("  ✓ cycle complet de commentaire")

            # ⚠️ point 3 : l'hôte content.airtable.com
            att_field = os.environ.get("AIRTABLE_TEST_ATTACHMENT_FIELD")
            if att_field:
                print(f"→ airtable_attachment(field={att_field!r}) — hôte content.*")
                try:
                    payload = base64.b64encode(b"colonne,valeur\nsonde,1\n").decode()
                    attachment_tool(
                        base_id=base_id, record_id=created[0], field=att_field,
                        filename=f"{stamp}.csv", content_type="text/csv",
                        file_base64=payload)
                    _verdict("uploadAttachment sur content.airtable.com", True)
                except Exception as e:  # noqa: BLE001
                    _verdict("uploadAttachment sur content.airtable.com", False,
                             str(e)[:180])
            else:
                _verdict("uploadAttachment sur content.airtable.com", False,
                         "non exercé (AIRTABLE_TEST_ATTACHMENT_FIELD absent)")

            # ⚠️ point 2 : les PATCH de schéma
            if schema_write:
                print("\n→ écritures de SCHÉMA (irréversibles par l'API)")
                new_field = field_tool(
                    base_id=base_id, table_id=table_id, op="create",
                    name=f"{stamp}-champ", type="singleLineText",
                    description="créé par le smoke test oto")
                print(f"  ✓ champ créé : {new_field.get('id')}")
                try:
                    field_tool(base_id=base_id, table_id=table_id, op="update",
                               field_id=new_field["id"],
                               description="renommé par le smoke test oto")
                    _verdict("PATCH …/fields/{fieldId}", True)
                except Exception as e:  # noqa: BLE001
                    _verdict("PATCH …/fields/{fieldId}", False, str(e)[:180])
                try:
                    current = next(t for t in table_tool(
                        base_id=base_id, table_id=table_id)["tables"])
                    table_tool(base_id=base_id, op="update", table_id=table_id,
                               description=f"{stamp} — sonde")
                    table_tool(base_id=base_id, op="update", table_id=table_id,
                               description=current.get("description") or " ")
                    _verdict("PATCH …/tables/{tableId}", True)
                except Exception as e:  # noqa: BLE001
                    _verdict("PATCH …/tables/{tableId}", False, str(e)[:180])
                print(f"  ⚠️ à nettoyer À LA MAIN : le champ {stamp!r}-champ "
                      f"(l'API Airtable ne sait pas supprimer un champ)")
            else:
                _verdict("PATCH …/tables/{tableId}", False,
                         "non exercé (AIRTABLE_SMOKE_SCHEMA absent)")
                _verdict("PATCH …/fields/{fieldId}", False,
                         "non exercé (AIRTABLE_SMOKE_SCHEMA absent)")
        finally:
            # Le nettoyage RELIT la table au lieu de se fier aux ids accumulés : si
            # l'upsert a créé des lignes puis levé, `created` ne les connaît pas, et
            # elles resteraient dans une base réelle. Le tampon est la seule référence
            # qui survit à n'importe quel point de sortie.
            print(f"\n→ nettoyage : recherche des lignes {stamp!r}")
            try:
                leftovers = record_tool(
                    base_id=base_id, table=table_id, max_records=200,
                    filter_by_formula=f"FIND('{stamp}', {{{primary_name}}}) > 0")
                ids = [r["id"] for r in leftovers["records"]]
                unknown = [i for i in ids if i not in created]
                if unknown:
                    print(f"  (dont {len(unknown)} ligne(s) qu'aucun id accumulé ne "
                          f"connaissait — c'est précisément pourquoi on relit)")
                if not ids:
                    print("  ✓ rien à supprimer")
                else:
                    gone = record_tool(base_id=base_id, table=table_id, op="delete",
                                       record_ids=ids)
                    print(f"  ✓ {gone['succeeded']}/{gone['total']} supprimée(s)"
                          + (f" — RESTE : {gone['failed']}" if gone["failed"] else ""))
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ nettoyage impossible ({str(e)[:160]}) — supprimer À LA MAIN "
                      f"les lignes contenant {stamp!r}, ids connus : {created}")

    print("\nVerdicts :")
    print("\n".join(_VERDICTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
