## prerequisite — ton token api + company id recruitee

recruitee demande **deux champs** :
- `api_token` — ton token api personnel (recruitee, **settings → apps & plugins → personal API tokens**)
- `company_id` — l'identifiant de ta société recruitee (visible dans l'url de ton espace, ex. `recruitee.com/c/<company_id>`)
renseigne les deux dans tes [clés de connecteurs](https://manage.oto.cx/).
- doc éditeur : [recruitee.com](https://www.recruitee.com)

## usage — ce que tu peux faire

pilote ton ats recruitee : un poste = une **offer**, un candidat est rattaché à des offers.
- « liste les candidats du poste 12 » → `recruitee_candidates` (filtres `offer_id`, `query` par nom/email), détail → `recruitee_candidate`
- « crée un candidat et attache-le à l'offer 12 » → `recruitee_create_candidate` (`offer_ids`)
- « ajoute une note sur ce candidat » → `recruitee_add_note`
- « liste mes offres actives » → `recruitee_offers` (`scope` active/archived, `kind` job/talent_pool), détail → `recruitee_offer`
