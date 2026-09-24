# Ticket playbooks for Azure Boards

> **Illustrative example only. Not tested or verified against Azure DevOps.**
> These playbooks show how job templates could manage Azure Boards tickets through the `ado.workitems` roles. They have not been run against a live Azure DevOps organization or an automation controller. Only the included mock server has exercised them. Test them in a non-production Azure DevOps organization first.

Each playbook starts with comments that list the job template settings and survey questions it needs. This page covers the setup that all of the playbooks share. Do it once.

| Playbook | Job template name | What the job does |
|---|---|---|
| `create_ticket.yml` | ADO - Create ticket | Opens a ticket, or finds the existing ticket that has the same reference tag. Publishes `ticket_id` for workflows. |
| `update_ticket.yml` | ADO - Update ticket | Changes title, description, priority, tags, area path, iteration path or any field. |
| `comment_ticket.yml` | ADO - Comment on ticket | Adds a discussion comment with the automation controller job ID. |
| `assign_ticket.yml` | ADO - Assign ticket | Assigns the ticket and records a note. |
| `transition_ticket.yml` | ADO - Transition ticket | Moves the ticket to a new state, for example Resolved. |
| `link_tickets.yml` | ADO - Link tickets | Adds or removes parent, child, related, dependency and duplicate links. |
| `query_tickets.yml` | ADO - Query tickets | Read-only report of matching tickets. Publishes `ticket_ids`. |
| `delete_ticket.yml` | ADO - Delete ticket | Moves a ticket to the recycle bin, or destroys it, after the operator types the ID twice. |
| `open_ticket_on_failure.yml` | ADO - Open ticket on failure | Runs on a workflow failure path and opens one ticket per failed workflow job. |

## 1. Prepare Azure DevOps

Pick one authentication method.

- **Service principal (recommended).** Register an application in Microsoft Entra ID and create a client secret. In Azure DevOps, go to Organization settings > Users, add the application, give it the Basic access level (Stakeholder is enough for work items only), and add it to each project the job templates will touch. Without that step Azure DevOps answers with error TF401444.
- **Personal access token.** Create a token with the Work Items (Read, write and manage) scope. The token acts as the person who created it, and it expires, so use it for demonstrations rather than shared automation.
- **Managed identity.** Only when the execution nodes are Azure virtual machines with a managed identity assigned. Add the identity to the organization the same way as a service principal.

## 2. Create the credential type

The native Microsoft Azure Resource Manager credential carries no Azure DevOps organization, so the collection adds a custom credential type, "Azure DevOps Organization". Creating credential types needs the System administrator role.

- **With automation:** run `playbooks/configure_credential_type.yml` as a job template with a Red Hat Ansible Automation Platform credential attached. The execution environment needs the `ansible.controller` collection, which the supported execution environment includes.
- **By hand:** go to Automation Execution > Infrastructure > Credential Types, click Create credential type, and paste the `inputs` and `injectors` sections of `docs/credential_types/azure_devops_organization.yml` into the Input configuration and Injector configuration fields. See [getting started with credential types](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/secure-proc_get_started_credential_types).

## 3. Create the credentials

Go to Automation Execution > Infrastructure > Credentials.

| Credential | Credential type | Fields |
|---|---|---|
| Azure DevOps - contoso | Azure DevOps Organization | Organization name, default project, and a personal access token only if you chose that method |
| Azure DevOps service principal | [Microsoft Azure Resource Manager](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/secure-ref_controller_credential_azure_resource) | Subscription ID (any subscription in the tenant; the roles do not use it), Client ID, Client Secret, Tenant ID |

Grant Use on the credentials to the teams that will build the job templates, and nothing more. Operators who only launch jobs never see the secrets.

## 4. Create the project

| Setting | Value |
|---|---|
| Name | ado.workitems |
| Source control type | Git |
| Source control URL | `https://github.com/mkbredem/ado.workitems.git` |
| Source control branch | main |
| Options | Update revision on launch (optional) |

The project sync installs the collection itself from `collections/requirements.yml`, so the roles resolve without adding the collection to an execution environment. If the repository is private, add a source control credential to the project. The project sync also needs network access to github.com.

## 5. Create the job templates

Create one job template per playbook, using the settings in the comments at the top of each playbook. All of them share:

- Job type Run. The Check job type asks Azure DevOps to validate creates and updates with `validateOnly=true` and saves nothing, which is a safe way to show a change before making it.
- Any inventory. The plays run on `localhost` inside the execution environment.
- The Minimal execution environment, or any other one. The roles use only `ansible.builtin` modules.
- The Azure DevOps Organization credential, plus the Microsoft Azure Resource Manager credential when you use a service principal.

Then add the survey from the playbook comments and turn the survey on.

## 6. Build workflows

The create, query and failure playbooks publish `ticket_id` or `ticket_ids` with `set_stats`. Automation controller passes those [workflow artifacts](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/develop-assembly_ug_controller_workflows) to every downstream node, so leave `ticket_id` out of the surveys of templates you use downstream.

Example remediation workflow:

```
ADO - Create ticket
  └─ always ─ Remediation job template
                ├─ success ─ ADO - Transition ticket  (extra variables: ticket_state: Resolved)
                └─ failure ─ ADO - Comment on ticket  (extra variables: ticket_comment: Remediation failed, see job output)
```

For approvals, put an approval node before "ADO - Transition ticket". When two branches that both set `ticket_id` converge on one node, automation controller merges their artifacts in an undefined order, so avoid converging branches that open different tickets.

## 7. Control access

- Give operators Execute on the create, comment, assign, transition, link and query templates.
- Keep Execute on "ADO - Delete ticket" for the team that owns ticket hygiene.
- Automation controller records who launched every job, so the Azure Boards history ("changed by" the service principal) and the job list together show which person made each change. A shared personal access token cannot show that.
