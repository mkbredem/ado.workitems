# ado.workitems changelog

## 1.0.0

- Initial release, created as an illustrative example of abstracting the
  Azure DevOps REST API with roles. Not tested or verified against a live
  Azure DevOps organization; tested only against the included mock server.
- Roles: auth, api, work_item, work_item_info, comment, attachment, link, batch.
- Authentication with the native Microsoft Azure Resource Manager credential
  (service principal), managed identity, or personal access token through the
  Azure DevOps Organization custom credential type.
- Offline mock server and idempotency tests.
