## prerequisite — ta clé api brevo (v3)

brevo s'authentifie via une **clé api v3**. dans [ton compte brevo](https://app.brevo.com), va dans **paramètres → smtp & api → clés api**, génère une clé (elle porte tout le compte, pas de scope).
- copie la clé (elle commence par `xkeysib-`)
- colle-la dans oto sur ton compte (`/account`), connecteur **brevo**
- byo uniquement : ta clé ou celle partagée de ton org, pas de clé plateforme
- à ne pas confondre avec **brevo (automation)**, un connecteur distinct pour les scénarios d'automation (connexion par session navigateur)

## usage — emailing & crm depuis claude

gère ta base contacts, tes envois et ton crm brevo.
- « ajoute jean à la liste newsletter » → `brevo_upsert_contact` / `brevo_list_membership`
- « envoie cet email à marie » → `brevo_send_email` (transactionnel unitaire)
- « prépare une campagne pour la liste clients » → `brevo_create_campaign` (brouillon ; l'envoi de masse se déclenche dans l'ui)
- « combien d'ouvertures sur ma dernière campagne » → `brevo_campaigns` (statistics)
- « crée un deal à 10k€ » → `brevo_crm_create` (entity `deals`)
