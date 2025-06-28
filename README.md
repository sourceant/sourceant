# 🐜 SourceAnt 🐜
**SourceAnt** is an open-source tool that automates code reviews by integrating GitHub webhooks with the AI-model APIs. It listens for pull request events, analyzes code changes and posts review feedback as comments on GitHub pull requests.

## Features ✨
- **Automated Code Reviews**: Analyze pull requests automatically using the configured LLM.
- **Dynamic Review Process**: Intelligently handles large diffs by summarizing the entire PR and then reviewing file-by-file with global context.
- **GitHub Integration**: Seamlessly integrates with GitHub webhooks.
- **Customizable Feedback**: Post detailed, actionable feedback on pull requests.
- **Open Source**: Fully open-source and community-driven.


## Getting Started 🛠️

### Prerequisites
- Python 3.8+
- GitHub account with a repository for testing.
- LLM API key (Currently supports Gemini).
- GitHub personal access token with `repo` scope.

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
   GEMINI_API_KEY=your_gemini_api_key
   GITHUB_TOKEN=your_github_personal_access_token
   GEMINI_MODEL=gemini-2.5-flash
   GEMINI_TOKEN_LIMIT=100000
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
- [ ] Integrate DeepSeek API for code analysis.
- [ ] Implement a dashboard for review history and metrics.
- [ ] Add CI/CD pipeline for automated testing and deployment.

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

