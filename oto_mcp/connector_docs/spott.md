## prerequisite — ta clé api spott

il te faut une **clé api spott**.
- dans spott, va dans **settings → api keys** et génère une clé
- colle-la dans tes [clés de connecteurs](https://manage.oto.cx/) (ou laisse ton org partager la sienne)
- doc éditeur : [api-docs.spott.io](https://api-docs.spott.io)

## usage — ce que tu peux faire

pilote spott, l'ats **et** le crm d'un cabinet de recrutement : le candidat d'un côté, l'entreprise cliente de l'autre. un poste = un **job** (`vacancy` dans les urls de l'api), un candidat sur un poste = une **application** qui avance d'**étape** en étape.
- « est-ce qu'on connaît déjà jean dupont ? » → `spott_people` (cherche candidats **et** contacts clients, flou) — à faire avant de créer quoi que ce soit
- « liste les candidats » → `spott_candidates`, détail → `spott_candidate` ; par critères → `spott_search_candidates`
- « crée un candidat » → `spott_create_candidate` (`firstName`/`lastName` obligatoires), correction → `spott_update_candidate`
- « quels postes sont ouverts ? » → `spott_search_jobs` avec le filtre `vacancy.stage.isOpen` ; la liste brute → `spott_jobs`, détail → `spott_job`
- « où en sont les candidatures du poste X ? » → `spott_applications` (`job_id`, ou `candidate_id` pour l'inverse)
- « fais postuler ce candidat » → `spott_stages` (récupérer l'id d'étape) puis `spott_create_application` ; « passe-le en entretien » → `spott_move_application`
- « note l'appel d'hier » → `spott_create_note` (`links` vers le candidat, `source` phone/inPerson…), relire → `spott_notes`
- côté crm : `spott_clients` (liste, ou recherche si tu passes `filters`), `spott_client`, `spott_client_contacts`, et `spott_placements` pour les placements conclus et leurs honoraires
