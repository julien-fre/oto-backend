## prerequisite — the Microsoft 365 account the services borrow

this connector is the **account**: the Microsoft address, its token, the directory it signs in to, the default account. each Microsoft service — SharePoint & OneDrive, Outlook, Outlook Calendar, Teams — is a connector in its own right, with **its own** consent (its permissions only): authorise it from its card, on the same account. Microsoft adds each new consent to the ones already given: one account, one connection, several services.
- you can link **several** Microsoft accounts; each service tool acts on the one the call names with `_account=<name>`, otherwise the one the project pins — on the service's card, otherwise on this one —, otherwise the only one, otherwise the default. an unknown name is refused, never replaced by another
- "linking an account" here only asks for your identity: the services add their permissions from their cards
- work or school accounts only, on the directory of their organization (personal Microsoft accounts have no SharePoint, Outlook for business or Teams of their own)

## setup — a guest account in a client's directory

when a client has invited you into THEIR Microsoft 365 (you are a guest of their directory, with a personal Microsoft account or another company's account), the ordinary sign-in lands in your own directory, or fails. In the **Client directory** field of the connection, give the client's directory: its Microsoft domain (`contoso.onmicrosoft.com`, or a domain of theirs like `contoso.com`), its directory ID, or simply the address of one of its SharePoint sites (`https://contoso.sharepoint.com/sites/...`).
- ⚠️ from a SharePoint address, oto deduces `contoso.onmicrosoft.com`: a convention that almost always holds. If Microsoft says the directory does not exist, ask the client's administrator for the exact domain or the directory ID (Entra admin center, Overview)
- the account is then named by its address followed by the directory in brackets, and every renewal goes back to that directory

## note — when the organization requires an administrator

many organizations forbid a person to authorise an application alone: Microsoft then says "admin approval required", and the card comes back with that reason. A Microsoft 365 administrator of the organization must approve oto once, for everyone:
- reading the messages of a Teams **channel** always needs this approval, even where people may authorise the rest alone (see the "Teams" connector)
- `microsoft_admin_consent()` returns the approval link (`url`, valid seven days) to send to that administrator — `services` to approve (default: all the Microsoft services), `tenant` the organization's domain or a SharePoint address of theirs (default: the administrator's own directory)
- or the administrator connects from the same card and ticks "consent on behalf of your organization" on Microsoft's screen
- then each person connects from the card as usual. Approving gives nobody access to anything: each person still acts with their own rights

## usage — which accounts, with which services

`microsoft_accounts` — the linked accounts and, for each, the services it has authorised and the client's directory it signs in to. This is the question to ask when a service tool refuses: the account exists but has not yet authorised THIS service — connect it from the service's card.
- "which Microsoft accounts have I linked, and which have SharePoint?"
- the default account: `oto_identity(op='set', connector='microsoft', identity_id='<account>')`
