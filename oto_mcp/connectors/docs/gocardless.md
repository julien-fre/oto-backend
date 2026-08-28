## prerequisite — ta clé api gocardless

lecture seule — chaque utilisateur pose sa propre clé, tes prélèvements ne sont visibles que par toi.
- depuis le [dashboard gocardless](https://gocardless.com), ouvre developers puis create access token
- choisis un token en **lecture** (read-only) — oto n'annule ni ne crée de prélèvement
- colle-le dans tes clés de connecteur oto sous `gocardless`

## usage — suivre prélèvements et échecs sepa

consulte tes prélèvements, leur timeline et les motifs d'échec pour la réconciliation.
- `gocardless_payments` liste les prélèvements (filtre par `status`, mandat, customer, date)
- `gocardless_failed` te sort en un appel les prélèvements refusés enrichis (client, montant, cause, `will_attempt_retry`)
- `gocardless_failure_reason` donne le motif du dernier échec d'un paiement précis (`PM…`)
- `gocardless_payment_party` résout paiement → mandat → client (email, société)
