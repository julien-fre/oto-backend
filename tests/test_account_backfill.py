"""R1c — renommage de compte du coffre (backfill '' -> label nommé au passage
multi-compte). Re-chiffrement obligatoire : l'AAD lie le ciphertext à son `account`.

⚠️ Depuis L6 pièce 2, le geste rend un `RenameOutcome` et non plus un booléen nu :
un renommage qui trouve une instance vivante déjà installée à l'arrivée ARCHIVE
celle du départ, et cela ne doit jamais être silencieux. L'objet reste booléen à
l'usage (`__bool__` = « la ligne de coffre a bougé »), d'où `bool(...)` ici. Le
déplacement de l'instance lui-même est exercé contre un vrai PostgreSQL par
`test_connector_instances_birth_live.py` — un stub n'a pas de table à déplacer.
"""
from oto_mcp import credentials_store as cs


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_rename_account_reencrypts(monkeypatch):
    monkeypatch.setattr(cs, "get_credential_with_meta",
                        lambda et, eid, con, account="":
                        {"secret": "S", "meta": {"is_default": True}, "set_at": None})
    monkeypatch.setattr(cs, "_connect", lambda: _Conn())
    ups, dels = [], []
    monkeypatch.setattr(cs, "_upsert",
                        lambda c, et, eid, con, acct, sec, sb, meta: ups.append((acct, sec, meta)))
    monkeypatch.setattr(cs, "_delete", lambda c, et, eid, con, acct: dels.append(acct))
    monkeypatch.setattr(cs.connector_instances, "move_instance_to_account",
                        lambda *a, **k: (True, None, 7))
    issue = cs.rename_account("member", "1:u", "zoho", "", "principal")
    assert bool(issue) is True and issue.moved is True and issue.kept_instance_id == 7
    assert ups == [("principal", "S", {"is_default": True})]  # re-posé sous le nouveau nom
    assert dels == [""]                                       # ancienne ligne supprimée


def test_rename_account_missing_returns_false(monkeypatch):
    monkeypatch.setattr(cs, "get_credential_with_meta", lambda *a, **k: None)
    issue = cs.rename_account("member", "1:u", "zoho", "", "principal")
    assert bool(issue) is False and issue.moved is False
