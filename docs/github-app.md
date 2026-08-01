## GitHub App Setup

SourceAnt reaches a repository as a GitHub App: it receives events for the repositories it is installed on, and acts with the permissions you granted, per installation.

### Using SourceAnt Cloud

Install the app and pick your repositories:

**[Install the SourceAnt GitHub App](https://github.com/apps/sourceant)**

Events go to the cloud backend, and there is nothing further to configure.

### Running your own

A self-hosted instance needs its own app, because the webhook has to point at your server.

#### 1. Create the app

**GitHub Settings > Developer settings > GitHub Apps > New GitHub App**.

- **Webhook URL:** your instance's webhook endpoint, `https://your-domain.com/api/prs/github-webhook`.
- **Webhook secret:** generate one. This is the `GITHUB_SECRET` environment variable, and SourceAnt rejects a delivery whose signature does not match it.

#### 2. Grant permissions

| Permission | Access | Needed for |
|---|---|---|
| Contents | Read-only | Reading the diff and any manifests |
| Pull requests | Read and write | Posting reviews and review comments |
| Issues | Read and write | Issue comments and labels, used by triage and the repo manager |
| Metadata | Read-only | Mandatory for every app |

Issues access is only needed if you use [triage](triage.md) or the [repo manager](repo-management.md). Without it, code review still works.

#### 3. Subscribe to events

Under **Subscribe to events**, select **Pull request** and **Issues**. SourceAnt acts on `opened`, `reopened`, `synchronize`, and `ready_for_review` for pull requests, and `opened` and `reopened` for issues. Nothing happens without these.

#### 4. Generate a private key

At the bottom of the app settings page, generate a private key and save the `.pem` file where your instance can read it.

#### 5. Configure the instance

```env
GITHUB_APP_ID=123456
GITHUB_APP_CLIENT_ID=Iv23liOAxxxxxM88Sqy97
GITHUB_APP_PRIVATE_KEY_PATH=/path/to/private-key.pem
GITHUB_SECRET=your_webhook_secret
```

The app id and client id are on the app's settings page. All three app values are required; SourceAnt refuses to start its GitHub integration without them.

#### 6. Install it

From the app's page, install it on the repositories you want reviewed. Opening a pull request on one of them should produce a review shortly after.

### Repository webhook instead

A single repository can be pointed at SourceAnt without an app, though the app credentials above are still needed to post anything back.

1. Repository **Settings > Webhooks > Add webhook**.
2. **Payload URL:** `https://your-domain.com/api/prs/github-webhook`.
3. **Content type:** `application/json`.
4. **Secret:** the same value as `GITHUB_SECRET`.
5. **Events:** select **Pull requests** and **Issues**.

Repositories connected through GitHub OAuth deliver to `/api/prs/github-webhook-oauth` instead, which verifies against `GITHUB_OAUTH_SECRET`.
