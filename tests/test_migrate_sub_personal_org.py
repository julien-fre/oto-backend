"""La fusion de deux comptes doit emporter la MARQUE d'espace personnel.

Vécu le 2026-08-14, en prod, sur 14 comptes : `orgs.personal_of` restait accroché
à l'identifiant de l'ancien compte, que l'étape 4 du merge supprime. Le compte
survivant n'avait donc plus d'espace personnel trouvable, et le boot suivant lui en
fabriquait un neuf — deux organisations au même nom dans sa liste, dont l'ancienne,
celle qui porte son historique, n'était plus reconnue comme la sienne. Neuf des
quatorze cas venaient d'une seule bascule de tenant (13/08).

La colonne échappe aux deux garde-fous existants : ce n'est pas une clé étrangère
(`test_migrate_sub_cascade`) et elle n'est pas dans l'inventaire des colonnes
repointées (`test_migrate_sub_inventory` vérifie que les entrées LISTÉES existent,
pas que les colonnes porteuses d'un identifiant soient listées). D'où ce test de
COMPORTEMENT, qui exerce le merge lui-même.
"""
import oto_mcp.db.users as users


class _Conn:
    """Faux connecteur : enregistre le SQL, sert les lignes qu'on lui dicte."""

    def __init__(self, perso_ancienne):
        self.sql = []
        self._perso = perso_ancienne

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def transaction(self):
        return self

    def execute(self, sql, params=None):
        plat = " ".join(sql.split())
        self.sql.append((plat, params))
        row = None
        if plat.startswith("SELECT * FROM users WHERE sub="):
            row = {"sub": params[0], "role": "member", "avatar_url": None}
        elif plat.startswith("SELECT role FROM users WHERE sub="):
            row = {"role": "member"}
        elif "SELECT id FROM orgs WHERE personal_of=" in plat:
            row = self._perso
        conn = self

        class _R:
            def fetchone(self):
                return row

            def fetchall(self):
                # les orgs DÉMARQUÉES (RETURNING id) — une seule dans nos cas
                if "SET personal_of=NULL WHERE personal_of=" in plat:
                    return conn.demarquees
                return []

        return _R()


def _merge(monkeypatch, *, perso_ancienne, demarquees):
    conn = _Conn(perso_ancienne)
    conn.demarquees = demarquees
    monkeypatch.setattr(users, "_connect", lambda: conn)

    class _Tenants:
        def same_tenant(self, a, b):
            return True

    monkeypatch.setattr("oto_mcp.tenancy.current", lambda: _Tenants())
    assert users.migrate_sub("ancien", "nouveau") is True
    return [s for s in conn.sql if "personal_of" in s[0]]


def test_la_marque_suit_le_compte_survivant(monkeypatch):
    """L'espace de l'ancien compte reste l'espace personnel, sous le nouvel
    identifiant — et celui du nouveau compte, créé par son login quelques
    secondes plus tôt, est démarqué pour libérer le slot unique."""
    sql = _merge(monkeypatch, perso_ancienne={"id": 90}, demarquees=[{"id": 244}])

    assert sql == [
        ("SELECT id FROM orgs WHERE personal_of=%s AND archived_at IS NULL",
         ("ancien",)),
        ("UPDATE orgs SET personal_of=NULL WHERE personal_of=%s "
         "AND archived_at IS NULL RETURNING id", ("nouveau",)),
        ("UPDATE orgs SET personal_of=%s WHERE id=%s", ("nouveau", 90)),
    ], "l'ordre compte : libérer le slot AVANT de le reprendre (index unique)"


def test_sans_espace_a_reprendre_on_ne_touche_a_rien(monkeypatch):
    """Ancien compte sans espace personnel (ou déjà archivé) : celui du compte
    survivant reste le sien. Ne jamais le démarquer « au cas où » — il serait
    recréé au boot suivant, ce qui est précisément le défaut qu'on ferme."""
    assert _merge(monkeypatch, perso_ancienne=None, demarquees=[]) == [
        ("SELECT id FROM orgs WHERE personal_of=%s AND archived_at IS NULL",
         ("ancien",)),
    ]
