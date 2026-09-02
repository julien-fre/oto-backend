# Relance des comptes jamais actifs (2026-09-02)

`oto_admin_outreach` (MCP) + `POST /api/admin/outreach` (REST). Le code : capacité
`capabilities/outreach.py`, requête `db/outreach.py`, DDL `db/schema/outreach.py`,
jeton et page de désinscription `outreach_optout.py` + route publique `/o/u/{token}`.

La plateforme savait **compter** ses comptes inactifs (`oto_admin_monitoring op=funnel`
→ `never_active`, `db.usage.activation_funnel`) sans jamais pouvoir les **nommer** ni
leur écrire. Ce lot ferme l'écart. L'envoi réutilise le chemin existant
(`email.send_composed_email` → `mailer.oto.zone`, Scaleway TEM) : **il n'y a pas de
second chemin d'envoi**.

## Le piège de comptage, mesuré avant d'écrire une ligne

Sur 78 organisations directes vivantes, **64 sont des espaces personnels créés d'office
à l'inscription** — pas des espaces que quelqu'un a voulus. Compter l'inactivité **par
organisation** revient donc à écrire à quelqu'un au sujet d'un espace qu'il n'a jamais
demandé, et le message n'a alors aucun sens pour lui.

⟹ **tout se compte par COMPTE (`users.sub`)**, jamais par org.

## Deux populations, à ne pas confondre

| `status` | définition | mesuré le 2026-09-02 |
|---|---|---|
| `never_active` | aucune ligne `tool_calls` de `kind='mcp'` | **40** comptes |
| `dormant` | a appelé, puis plus rien depuis `dormant_days` | 10 à 7 j, 6 à 14 j, **0** à 30 j |

Le message n'est pas le même, donc les deux ne se mélangent pas dans une audience.
Une trace `kind='rest'` ou `'protocol'` (dashboard ouvert, handshake MCP) **ne compte
pas** comme un usage : 17 des 40 sont dans ce cas — venus, repartis sans rien demander.

## L'exclusion du tenant partenaire est dans la REQUÊTE

Les comptes hébergés chez un tenant tiers sont **les clients de ce tenant**. Leur
écrire, c'est parler par-dessus lui, dans son produit. L'exclusion ne peut donc pas
être une consigne : elle vit dans `_AUDIENCE_SQL`, **en amont de tout critère
d'activité**, et `tests/test_outreach_audience_db.py` la mute pour prouver qu'elle mord.

⚠️ **Le discriminant n'est PAS `orgs.tenant_id`** — mesuré INERTE : les 160 orgs
portent le tenant primaire, partenaires compris (le provisioning ne l'écrit pas). Deux
axes portent, en UNION :

1. la **qualification du sub** (`tenancy.qualify`, préfixe `<slug>:`) — 22 comptes ;
2. l'appartenance à au moins une org dont le tenant EFFECTIF est le nôtre
   (`tenants._ORG_TENANT_EXPR`, **source unique** partagée avec `org_tenant_slug`).

Le (2) couvre l'angle mort du (1) : un compte inscrit chez nous, invité uniquement dans
des orgs de partenaire. **Mesuré à 0 aujourd'hui**, ce qui ne dit rien de demain.

⚠️ **(1) est aujourd'hui REDONDANT avec (2)** — un sub qualifié est toujours membre
d'une org que sa seule présence fait lire comme celle du partenaire. Gardé en
profondeur. **Corollaire à connaître : un seul membre qualifié suffit à sortir TOUTE
une org de l'audience**, ses membres à nous compris. Sur-exclusion assumée (rater une
relance ne coûte rien, écrire aux clients d'un tiers coûte le partenariat), mais elle
peut vider une audience sans rien dire — si le compte servi paraît trop petit, c'est là
qu'il faut regarder.

## La langue : ce qui existe vraiment, et ce qui n'existe pas

**Réponse à la question laissée ouverte par `email.md` §Locale** (« détection de langue
pour un contact jamais loggé — question ouverte »). Relevé sur les 64 comptes de notre
tenant, et sur les 40 de l'audience :

| signal | couverture (64 comptes) | sur l'audience (40) | verdict |
|---|---|---|---|
| `users.locale` (préférence d'UI du dashboard) | 11 (9 fr, 2 en) | **2** | le seul déclaré, quasi vide |
| `billing_identities.country_code` | **0 ligne en base** | 0 | inexistant |
| TLD de l'adresse | `.fr` sur 7 | 3 sur 39 | non concluant |
| domaine grand public (gmail, outlook…) | 15 | — | ne dit rien de la langue |

**Il n'existe aucun signal fiable de langue.** `users.locale` est une préférence
d'INTERFACE, pas une nationalité, et elle est posée sur 5 % de l'audience. Le TLD ne
tranche rien (un `.com` peut être français, un `.fr` une filiale) et **n'entre dans
aucune décision** — il est servi comme `email_domain`, pour l'œil de l'opérateur.

Conséquence assumée dans le contrat servi : la capacité rend `locale` (déclarée, souvent
`null`), `served_locale` et `locale_source` (`declared` | `default`), et l'opérateur
CHOISIT `default_locale` pour tous ceux qui n'ont pas déclaré. Les compteurs
`with_declared_locale` / `with_default_locale` disent combien tombent de chaque côté.

**Ce qui reste à faire pour mieux savoir** est un autre lot : demander la langue à
l'inscription (ou capter `Accept-Language` au premier login) et l'écrire dans
`users.locale`. Sans ça, aucune amélioration de l'algorithme ne changera quoi que ce soit.

## Les garde-fous, et pourquoi chacun est mécanique

| garde-fou | mécanisme | ce qu'il empêche |
|---|---|---|
| une seule relance par personne | index unique partiel `(campaign, sub) WHERE kind='send'`, **écrit AVANT l'envoi** | le doublon dans une boîte mail |
| rien ne part sans essai reçu | `op=send` exige un `op=test` portant la MÊME empreinte de contenu, **pour chaque langue servie** | envoyer un message qu'on n'a pas vu arriver chez soi |
| le nombre est annoncé | `op=send` sans `confirm` refuse en disant N ; `confirm` faux refuse | découvrir N après coup |
| plafond dur | `MAX_ENVOI = 200`, jugé sur `taille_audience()` — l'audience ENTIÈRE | l'envoi de masse non relu |
| le refus se respecte | lien signé `/o/u/<jeton>` → `outreach_optouts`, lu par l'audience | relancer qui s'est désinscrit |

⚠️ **Le plafond se juge sur le total, pas sur la liste servie** : la lecture tronque
déjà à `MAX_ENVOI`, donc un plafond comparé à la page serait vert pour toujours. D'où
`taille_audience()`, et les champs `total` / `selected` / `truncated`.

⚠️ **Toute retouche du texte invalide l'essai** (l'empreinte est un sha256 du sujet + du
corps + du CTA, toutes langues). C'est voulu : « je l'ai vu arriver » ne vaut que pour
le message qu'on a vu.

⚠️ **La trace précède l'envoi, et se retire si rien n'est parti** (`annule_envoi`) :
sans ce retrait, un hoquet du mailer sortirait la personne de toute audience future
alors qu'elle n'a jamais rien reçu.

## Le pied de page marketing a changé

`email.render_composed_email` accepte `locale` et `unsubscribe_url` (tous deux
additifs — sans eux, le rendu est **inchangé à l'octet près**). Avec un lien, la phrase
cesse de proposer « répondez pour ne plus en recevoir » : offrir deux chemins dont un
seul laisse une trace ferait croire à un refus enregistré qui ne l'est pas.

Le pied **transactionnel** (`email_brand.mention_transactionnelle`) continue de ne
proposer aucun désabonnement, délibérément : on ne se désabonne pas d'une invitation.

## Le lien de désinscription

Jeton HMAC-SHA256 sur le seul `sub`, **sans expiration** — le mail relu six mois plus
tard est précisément celui dont on ne veut plus, et « ce lien a expiré » transforme un
refus en corvée. Le risque est borné par ce qu'il autorise : cesser de recevoir nos
relances. Le contrôle de `typ` empêche qu'un jeton d'upload (même secret d'instance)
vaille désinscription.

La route `/o/u/{token}` est **anonyme et server-rendered**, sur le BACKEND
(`OTO_MCP_PUBLIC_URL`) : exiger une session la demanderait à celui-là même qui ne veut
plus rien avoir à faire avec nous, et un front indisponible ne doit pas bloquer un
refus. C'est un **GET qui écrit**, assumé : les clients mail ne postent pas, l'écriture
est idempotente et strictement soustractive.

Sans `OTO_MCP_OAUTH_STATE_SECRET`, `lien()` **lève** — plutôt qu'un lien mort dans le
pied de page de dizaines de mails.

## Autorisation

`oto_admin_outreach`, plancher `operator`. Lectures (`audience`, `preview`, `journal`,
`optouts`) = `PLATFORM_ADMIN` ; tout ce qui fait PARTIR un mail sous notre marque
(`test`, `send`) ou lève le refus de quelqu'un (`optout_clear`) = `SUPER_ADMIN`.
`tests/test_outreach_guards.py` **exerce** la règle pour chaque valeur de l'énuméré
`op` — une op ajoutée sans gate y arrive toute seule.

## Ce que ce lot ne fait pas

- **Pas d'écran de dashboard** : la route REST existe, la vue est du ressort d'`oto-front`.
- **Pas d'en-tête `List-Unsubscribe`** : le service d'envoi (`mailer.oto.zone`) n'accepte
  que `from`/`to`/`subject`/`html`/`reply_to`. Le lien dans le pied est le seul canal.
- **Pas de cadencement** : l'envoi est synchrone, borné par `MAX_ENVOI`. Il ne passe ni
  par `scheduled_emails` ni par les quiet hours (qui sont propres à `email_send` d'une
  org, cf. `email.md`).
- **Aucun envoi réel n'a été effectué** en construisant ce lot.
