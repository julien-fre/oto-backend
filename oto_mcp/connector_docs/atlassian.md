## prerequisite — autoriser le callback côté Atlassian

avant de connecter, un **admin** de ton org Atlassian doit autoriser l'URL de callback d'oto dans les réglages Rovo MCP Server (sinon le consentement OAuth échoue).
- url à autoriser : `{{callback:/api/atlassian/oauth/callback}}`
- où : [admin.atlassian.com → Security → Rovo MCP](https://admin.atlassian.com)
- [doc Atlassian](https://support.atlassian.com/security-and-access-policies/docs/control-atlassian-rovo-mcp-server-settings/)

## usage — ce que tu peux faire

pilote **Jira** et **Confluence** en langage naturel. par exemple :
- crée un ticket Jira dans un projet
- recherche des issues en JQL
- lis ou crée une page Confluence
