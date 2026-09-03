"""La description servie de `salesforce_record` promettait implicitement un filet de
déduplication que oto n'applique pas (signalements 684/685, 03/09/2026).

**Le cas réel.** Le 2026-09-03 à 08:42, `salesforce_record(op="bulk_create",
sobject="Contact")` a rendu `success: true` pour deux fiches qui étaient des
doublons EXACTS de fiches existantes sur le même `AccountId` (même prénom, même
nom, même compte). La procédure de l'org s'appuyait sur la règle native de
détection de doublons de Salesforce comme filet quand sa propre requête de
déduplication rate (dérive de format d'URL de profil : avec/sans `www`, ou un
permalien Sales Navigator au lieu d'une URL publique). Deux doublons créés EN
SILENCE dans le CRM d'un client, repérés seulement parce que l'agent savait par
ailleurs que ces personnes existaient.

**L'hypothèse du rapporteur ne tient pas, et c'est ce qui rend la mise en garde
délicate à écrire.** Il supposait que les règles de doublon ne s'appliquent pas
aux collections d'objets comme elles s'appliquent à une création unitaire. La
référence Salesforce de la ressource `POST composite/sobjects` montre l'inverse :
son exemple NORMATIF d'échec par enregistrement est précisément
`{"success": false, "errors": [{"statusCode": "DUPLICATES_DETECTED", …}]}`. Les
règles s'exécutent donc bien sur ce chemin ; ce qui manquait était leur
PARAMÉTRAGE dans l'org Salesforce du client. ⚠️ Écrire « le chemin groupé n'a pas
le filet du chemin unitaire » serait donc mentir dans l'autre sens : **aucun des
deux chemins ne porte de filet appliqué par oto** — ni `create`, ni `bulk_create`.

**Ce que le code fait, vérifié à la source.** `SalesforceClient.create_records`
(oto-core, `oto/tools/salesforce/client.py`) poste sur `composite/sobjects` et
`_request` construit ses en-têtes lui-même : `{"Authorization": …}`, rien d'autre.
Aucun `Sforce-Duplicate-Rule-Header` n'est envoyé, sur aucun chemin, et un
appelant ne peut pas en poser un (`_request(..., headers=…)` lève `TypeError`).
oto ne peut donc ni activer ces règles, ni constater qu'elles ont joué.

**Ce que la description servie disait.** La puce `bulk_create` vendait le chemin
groupé comme un substitut de N `op="create"` — « instead of N separate op="create"
calls » — sans nommer ce qu'aucun des deux ne porte. *Une description est une
instruction relue à chaque appel* : un agent qui compte sur un filet doit y lire
que ce filet n'est pas tendu par nous.

Ce banc verrouille cette phrase **dans le texte SERVI**, pas dans la docstring :
fastmcp jette toute prose placée après le bloc `Args:` (cf.
`test_docstring_prose_served.py`), donc une mise en garde bien écrite mais mal
placée n'atteindrait aucun agent — exactement le mode de panne qu'on corrige.
Éprouvé : déplacée après `Args:`, la clause disparaît du texte servi et les deux
cas ci-dessous rougissent.
"""
import asyncio

from fastmcp import FastMCP

from oto_mcp.tools import salesforce as S


def _description_servie(nom: str) -> str:
    """Le texte que `tools/list` rend au modèle — pas `inspect.getdoc`."""
    m = FastMCP("t")
    S.register(m)
    return asyncio.run(m.get_tool(nom)).description or ""


def _puce_bulk_create(description: str) -> str:
    """La puce `bulk_create` seule — le texte le plus proche du geste.

    Une mention perdue ailleurs dans la description ne compte pas : l'agent qui
    choisit `bulk_create` lit CETTE puce."""
    debut = description.index('**"bulk_create"**')
    fin = description.index('**"bulk_update"**', debut)
    return description[debut:fin]


def test_la_puce_bulk_create_nomme_le_filet_de_doublons_que_oto_napplique_pas():
    """Le doublon silencieux du 03/09 : la puce doit dire que la déduplication de
    Salesforce n'est pas un filet sur lequel s'appuyer ici."""
    puce = _puce_bulk_create(_description_servie("salesforce_record")).lower()
    assert "duplicate" in puce, (
        "la puce bulk_create ne parle pas de déduplication — un agent y lit "
        "« instead of N separate op=\"create\" calls » et en déduit l'équivalence")


def test_le_code_derreur_de_salesforce_est_nomme_dans_le_texte_servi():
    """`DUPLICATES_DETECTED` arrive en échec PAR ENREGISTREMENT dans `results`,
    jamais en exception : un agent qui ne connaît pas ce code ne le reconnaît pas
    dans le reçu. Le code d'erreur est un jeton d'API stable, pas de la prose —
    c'est ce qui rend ce cas vérifiable sans figer une tournure."""
    servie = _description_servie("salesforce_record")
    assert "DUPLICATES_DETECTED" in servie, (
        "le code que Salesforce rend quand une règle joue n'est pas nommé — et il "
        "n'atteint le modèle que s'il vit AVANT le bloc `Args:` du docstring")
