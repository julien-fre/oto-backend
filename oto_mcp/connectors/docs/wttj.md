## prerequisite — ton jeton api welcome to the jungle

il te faut un **jeton api** de l'ats welcome to the jungle (ex-welcome kit).
- il ne se génère pas dans l'interface : demande-le à wttj via [help.welcometothejungle.com](https://help.welcometothejungle.com/) en décrivant ton usage
- demande les scopes `me_r`, `organizations_r`, `jobs_r`, `candidates_rw` (ou `candidates_r` en lecture seule), `comments_w`, `moves_r`
- colle-le dans tes [clés de connecteurs](https://manage.oto.cx/) (ou laisse ton org partager le sien)
- doc éditeur : [developers.welcomekit.co](https://developers.welcomekit.co)

## usage — ce que tu peux faire

pilote ton ats wttj : tout part d'une **organisation**, une offre est un **job**, ses **étapes** se lisent sur le job, un **candidat** appartient à un job.
- « quelles organisations je vois ? » → `wttj_organization` (donne les `organization_reference`)
- « quelles offres sont publiées ? » → `wttj_job(op="list", status="published")` ; les étapes d'une offre → `wttj_job(op="get")`
- « qui est en entretien sur ce poste ? » → `wttj_candidate(op="list")` avec `job_reference` et `job_stage_id` ; détail → `wttj_candidate(op="get")`
- « ajoute ce candidat » → `wttj_candidate(op="create")` ; « passe-le à l'étape suivante » → `wttj_candidate(op="update", job_stage_id=…)` ; archiver → `archived=true`
- « note l'échange d'hier » → `wttj_comment` (écriture seule : l'api ne relit pas les commentaires)
- « qu'est-ce qui a bougé sur ce poste ? » → `wttj_moves` (par job ; peut exiger un scope de partenaire que wttj ne délivre pas aux clients)
