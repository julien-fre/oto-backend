## prerequisite — your silae paie api access

silae paie v1 uses oauth2 credentials with **three fields**; each firm/employer enters their own, and their payroll is visible only to them. ask your [silae](https://www.silae.fr) contact for them, or get them from your api space.
- `client_id` — the api application's identifier
- `client_secret` — the associated secret
- `subscription_key` — subscription key for the silae paie api
enter these three fields in your oto connector keys under `silae`

## usage — browse files, employees and payslips

read-only payroll (bank details are masked before they reach you).
- `silae_dossier(op="list")` lists the accessible payroll files, `silae_dossier(op="current_period")` the open period
- `silae_employee(op="list")` the employees of a file, `silae_employee` the detail of one employee by registration number
- `silae_payslip(op="list")` the payslips of a period, then `silae_payslip(op="header")` / `silae_payslip(op="lines")` / `silae_payslip(op="totals")` for the detail of one payslip
- `silae_variables_to_enter` the payroll variables (EVP) still to be entered on a file
