## GitHub App Setup

Authentication is handled via a GitHub App, which provides secure, repository-level access.

### Cloud Users

If you are using the official SourceAnt cloud service, install the GitHub App directly:

**[Install the SourceAnt GitHub App](https://github.com/apps/sourceant)**

The app will request the necessary permissions and automatically send events to the hosted backend. No further configuration is needed.

### Self-Hosted Users

If running your own instance, you must create your own GitHub App because the webhook URL must point to your server.

#### 1. Create a GitHub App

Navigate to **GitHub Settings > Developer settings > GitHub Apps > New GitHub App**.

- **Webhook URL:** Set to your backend's webhook endpoint, for example `https://your-domain.com/api/github/webhooks`.
- **Webhook Secret:** Generate a secure secret. This becomes your `GITHUB_SECRET` environment variable.

#### 2. Set Permissions

Under the **Permissions** tab, grant:

- **Repository permissions > Contents:** Read-only
- **Repository permissions > Pull requests:** Read and write

#### 3. Generate a Private Key

At the bottom of the app settings page, generate a new private key (`.pem` file). Save it securely and note the file path for the `GITHUB_APP_PRIVATE_KEY_PATH` environment variable.

#### 4. Configure Environment Variables

```env
GITHUB_APP_ID=123456
GITHUB_APP_PRIVATE_KEY_PATH=/path/to/private-key.pem
GITHUB_SECRET=your_webhook_secret
```

### Setting Up a Webhook

If you are not using the GitHub App flow, you can configure a webhook directly on a repository:

1. Go to your repository **Settings > Webhooks > Add webhook**.
2. **Payload URL:** Your server's `/webhook` endpoint.
3. **Content type:** `application/json`.
4. **Secret:** Your `GITHUB_WEBHOOK_SECRET`.
5. **Events:** Select **Pull requests** and **Issues**.
6. Save the webhook.
