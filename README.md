# 🐜 SourceAnt 🐜
**SourceAnt** is an open-source tool that automates code reviews by integrating GitHub webhooks with AI models. It listens for pull request events, analyzes code changes and posts review feedback as comments on GitHub pull requests.

## Features ✨
- **Multi-Model Support**: Use any [LiteLLM-compatible provider](https://docs.litellm.ai/docs/providers) — Gemini, Anthropic Claude, OpenAI, DeepSeek, Mistral, and [100+ more](https://docs.litellm.ai/docs/providers). Switch models with a single env var.
- **Automated Code Reviews**: Analyze pull requests automatically using the configured LLM.
- **Dynamic Review Process**: Intelligently handles large diffs by summarizing the entire PR and then reviewing file-by-file with global context.
- **GitHub Integration**: Seamlessly integrates with GitHub webhooks.
- **Customizable Feedback**: Post detailed, actionable feedback on pull requests.
- **Open Source**: Fully open-source and community-driven.


## Getting Started 🛠️

### Prerequisites
- Python 3.8+
- GitHub account with a repository for testing.
- LLM API key (supports any [LiteLLM-compatible provider](https://docs.litellm.ai/docs/providers): Gemini, Anthropic, DeepSeek, OpenAI, etc.).

### Installation
1. **Clone the Repository**:
   ```bash
   git clone https://github.com/sourceant/sourceant.git
   cd sourceant
   ```

2. **Initial project setup (docker-compose)**:
   ```bash
   docker compose up -d
   ```

3. **Install Dependencies**:
   ```bash
   docker compose exec app pip install -r requirements.txt
   ```

4. **Set Environment Variables**:
   Copy `.env.example` into `.env` file in the root directory and update the credentials accordingly:
   ```bash
   docker compose exec app cp .env.exmple .env
   ```
   #### `.env` file
   ```env
   GITHUB_WEBHOOK_SECRET=your_github_webhook_secret
   LLM_MODEL=gemini/gemini-2.5-flash
   LLM_TOKEN_LIMIT=1000000
   GEMINI_API_KEY=your_gemini_api_key
   ```
SourceAnt API should be live at http://localhost:8000


## SourceAnt Commands

The `sourceant` command provides the following subcommands for managing the application:

| Command               | Description                                      |
|-----------------------|--------------------------------------------------|
| `docker compose exec app sourceant db upgrade head`        | Set up database tables |
| `docker compose exec app sourceant db --help`                                 | See more database commands |


### Example Usage

- **Start the database**:
  ```bash
  sourceant db
  ```

- **Start the API server**:
  ```bash
  docker compose up -d
  ```

- **Run the Worker**:
  ```bash
  docker compose exec app rq worker --url redis://redis:6379
  ```

- **View logs**:
  ```bash
  docker compose logs
  ```

## Configuration
The application can be configured using environment variables. Key variables are documented in the `.env.example` file.

### LLM Model

SourceAnt uses [LiteLLM](https://docs.litellm.ai/docs/providers) to support 100+ LLM providers through a unified interface. Set the `LLM_MODEL` env var using the `provider/model` format:

| Provider | `LLM_MODEL` | API Key Env Var |
|----------|-------------|-----------------|
| Google Gemini | `gemini/gemini-2.5-flash` | `GEMINI_API_KEY` |
| Anthropic | `anthropic/claude-sonnet-4-5-20250929` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai/gpt-4o` | `OPENAI_API_KEY` |
| DeepSeek | `deepseek/deepseek-chat` | `DEEPSEEK_API_KEY` |

```bash
# Example: switch from Gemini to Anthropic
LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
ANTHROPIC_API_KEY=sk-ant-...
```

See the full list of supported providers in the [LiteLLM docs](https://docs.litellm.ai/docs/providers).

### GitHub App Setup

Authentication is handled via a GitHub App, which provides secure, repository-level access. Your setup path depends on whether you are using the official cloud service or self-hosting the backend.

#### For Cloud Users

If you are using the official SourceAnt cloud service, simply install our official GitHub App:

- **[Install the SourceAnt GitHub App](https://github.com/apps/sourceant)**

The app will request the necessary permissions, and once installed on your repositories, it will automatically send events to our hosted backend. No further configuration is needed.

#### For Self-Hosted Users

If you are running your own instance of SourceAnt (e.g., from this repository), you **must create your own GitHub App**. This is because the webhook URL must point to your own server.

1.  **Create a New GitHub App**:
    *   Navigate to your GitHub settings: **Developer settings** > **GitHub Apps** > **New GitHub App**.
    *   **Webhook URL**: Set this to the public URL of your backend, pointing to the webhook endpoint (e.g., `https://your-domain.com/api/github/webhooks`).
    *   **Webhook Secret**: Generate a secure secret and save it. You will need this for the `GITHUB_SECRET` environment variable.

2.  **Set Permissions**:
    Under the "Permissions" tab for your app, grant the following access:
    *   **Repository permissions** > **Contents**: `Read-only`
    *   **Repository permissions** > **Pull requests**: `Read & write`

3.  **Generate a Private Key**:
    *   At the bottom of your app's settings page, generate a new private key (`.pem` file).
    *   Save this file securely and note its path.

4.  **Configure Environment Variables**:
    Update your `.env` file with the credentials from the app you just created:
    *   `GITHUB_APP_ID`: The "App ID" from your app's settings page.
    *   `GITHUB_APP_PRIVATE_KEY_PATH`: The file path to the `.pem` private key you downloaded.
    *   `GITHUB_SECRET`: The webhook secret you created.

### Stateless Mode
For development, testing, or specific use cases where you want to process events without writing them to the database, you can enable stateless mode. In this mode, the application will not attempt to connect to or interact with any database, making it lighter and preventing data accumulation.

To enable stateless mode, set the following environment variable:
```bash
STATELESS_MODE=true
```

### Log Driver

The `LOG_DRIVER` environment variable controls where the application logs are sent. This is particularly useful in serverless environments where file-based logging is not practical.

-   **`console` (Default)**: Logs are sent to the console, intelligently routing to `stdout` for informational messages (`INFO`, `DEBUG`) and `stderr` for warnings and errors (`WARNING`, `ERROR`, `CRITICAL`). This is the recommended setting for serverless and containerized environments like Cloud Run and Docker.
-   **`file`**: Logs are written to `sourceant.log` in the root directory. This is useful for traditional deployments where you have access to the file system.
-   **`syslog`**: Logs are sent to the system's syslog daemon. This is suitable for environments where you want to centralize logs from multiple services into a single, system-level logging solution.

### Queue Mode

The application supports different backend modes for processing background jobs, controlled by the `QUEUE_MODE` environment variable.

-   **`redis` (Default)**: This is the recommended mode for production. It uses a persistent Redis queue (`rq`) to handle background tasks. This requires a separate `rq` worker process to be running.

-   **`redislite`**: A self-contained, file-based Redis queue. This mode is ideal for local development or testing as it provides the full functionality of a Redis queue without needing to run a separate Redis server. The Redis database file (`redislite.db`) will be created in the project root.

-   **`request`**: This mode uses FastAPI's `BackgroundTasks` to process jobs in the same process as the web request. It's the simplest mode for development as it requires no external worker or database, but it is not suitable for production as jobs will be lost if the server restarts.
    ```bash
    # Run the worker for redis mode
    docker compose exec app rq worker --url redis://redis:6379
    ```

-   **`request`**: This mode is suitable for development, testing, or lightweight deployments where setting up Redis is not desired. It uses FastAPI's built-in `BackgroundTasks` feature. Tasks are tied to the lifecycle of the HTTP request that triggers them and are executed by the web server process after the response has been sent. No separate worker is needed.

## Setting Up GitHub Webhook
1. Go to your GitHub repository.
2. Navigate to **Settings > Webhooks > Add Webhook**.
3. Set the **Payload URL** to your server's `/webhook` endpoint (e.g., `https://your-server.com/webhook`).
4. Set the **Content type** to `application/json`.
5. Add the `GITHUB_WEBHOOK_SECRET` to the **Secret** field.
6. Select **Let me select individual events** and choose **Pull requests**.
7. Save the webhook.

## Contributing 🤝
We welcome contributions! Here’s how you can help:
1. Fork the repository.
2. Create a new branch: `git checkout -b feature/your-feature`.
3. Make your changes and commit them: `git commit -m 'Add some feature'`.
4. Push to the branch: `git push origin feature/your-feature`.
5. Submit a pull request.

## Roadmap 🗺️
- [x] Set up FastAPI server and GitHub webhook integration.
- [x] Implement API/Interface to integrate various AI models
- [x] Integrate Gemini API for code analysis.
- [x] Multi-provider support via LiteLLM (Gemini, Anthropic, DeepSeek, OpenAI, etc.).
- [ ] Implement a dashboard for review history and metrics.
- [x] Add CI/CD pipeline for automated testing and deployment.

## License 📜
This project is licensed under the MIT License. See the [LICENSE](LICENSE.md) file for details.

## Contact 📧
Have questions or suggestions? Reach out to us:
- **Email**: opensource@nfebe.com
- **GitHub Issues**: [Open an issue](https://github.com/sourceant/sourceant/issues)

## Contributors ✨
Thanks to these amazing people who have contributed to this project:

<a href="https://github.com/your-username/sourceant/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=sourceant/sourceant" />
</a>

Made with ❤️ by [nfebe](https://github.com/nfebe).

