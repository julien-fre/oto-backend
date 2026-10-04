"""Le constat de `scripts/audit_sieges_580.py` : pur, sans base ni fournisseur."""
import pathlib
import sys
from datetime import timedelta

# La racine du dépôt n'est pas dans `sys.path` en CI (pas de `__init__.py`) : `scripts`
# ne s'importait que parce qu'un banc collecté avant l'y avait mise (oto#116).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from scripts.audit_sieges_580 import classer  # noqa: E402


LIE = "2026-09-20 10:00:00+00"


def _l(sub, aid, *, seat=True, mort=False, lie=LIE, org=1):
    return {"sub": sub, "org_id": org, "provider": "LINKEDIN", "account_id": aid,
            "platform_seat": seat, "connected_at": lie,
            "disconnected_at": "2026-09-21 00:00:00+00" if mort else None}


def test_le_constat_classe_chaque_liaison_vivante_de_la_cle_plateforme():
    lignes = [_l("a", "acc_neuf"), _l("b", "acc_vieux"), _l("c", "acc_sans_date"),
              _l("d", "acc_parti"), _l("e", "acc_byo", seat=False),
              _l("f", "acc_vieux", mort=True, org=2)]
    comptes = [{"id": "acc_neuf", "created_at": "2026-09-20 09:55:00+00", "name": "X"},
               {"id": "acc_vieux", "created_at": "2026-08-01 08:00:00+00", "name": "Y"},
               {"id": "acc_sans_date", "created_at": None},
               {"id": "acc_byo", "created_at": "2026-09-20 09:59:00+00"},
               {"id": "acc_orphelin", "created_at": "2026-09-01 00:00:00+00"}]
    c = classer(lignes, comptes, timedelta(minutes=70))
    assert c["compte"] == {"coherent": 1, "anterieur": 1, "date_manquante": 1, "absent": 1}
    assert c["orphelins_chez_le_fournisseur"] == 1
    vieux = c["a_examiner"]["anterieur"][0]
    assert (vieux["sub"], vieux["autres_subs"], vieux["autres_lignes_meme_sub"]) == ("b", 1, 0)
    assert "X" not in str(c) and "Y" not in str(c), "jamais un nom de compte"
