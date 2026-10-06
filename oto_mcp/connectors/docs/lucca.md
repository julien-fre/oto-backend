## prerequisite — your lucca api access

lucca uses a static api key and your instance's subdomain; each firm/employer enters their own, their data is only visible to them. generate the key from your lucca account or via your [lucca](https://www.lucca.fr) contact.
- `api_key` — api key generated in settings → api
- `domain` — only the subdomain of your instance (e.g. `acme` for acme.ilucca.net), not the full url
enter these two fields in your oto connector keys under `lucca`

## usage — browse the directory, absences, expenses and organization

read-only.
- `lucca_employee(op="list")` lists the reachable employees, `lucca_employee(op="get")` the detail of an employee by id
- `lucca_absence(op="list")` the absences posted over a period (`date` required; the free-text comment is cut by default, `fields=["*"]` returns it), `lucca_absence(op="get")` the detail of an absence
- `lucca_leave_request(op="list")` the leave requests (the approval workflow), `lucca_leave_request(op="get")` the detail of a request
- `lucca_expense_claim()` the expense claims — no detail by id, lucca only exposes the list
- `lucca_department(op="list")` the departments (employee lists are cut by default, `fields=["*"]` returns them), `lucca_department(op="get")` the detail of a department
- `lucca_establishment()` the establishments — no detail by id, base and pagination different from the rest; the nested legal entity is cut by default
