## Quick Start

SourceAnt automates code reviews and repository management by listening to GitHub webhook events and analyzing code changes with AI models.

### Prerequisites

- Python 3.8 or later
- Docker and Docker Compose
- A GitHub account with a repository for testing
- An LLM API key (Gemini, Anthropic, OpenAI, DeepSeek, or any LiteLLM-compatible provider)

### Installation

Clone the repository and start the services:

```bash
git clone https://github.com/sourceant/sourceant.git
cd sourceant
docker compose up -d
```

Install Python dependencies:

```bash
docker compose exec app pip install -r requirements.txt
```

### Configuration

Copy the environment file and configure your LLM provider and GitHub credentials:

```bash
docker compose exec app cp .env.example .env
```

### Database Setup

Run database migrations:

```bash
docker compose exec app sourceant db upgrade head
```

### Run the Worker

For Redis queue mode (default), start a background worker:

```bash
docker compose exec app rq worker --url redis://redis:6379
```

### Verify

SourceAnt API will be available at `http://localhost:8000`.

### Next Steps

- Configure a [GitHub App](github-app.md) to receive webhook events.
- Enable the [Repo Manager](repo-management.md) for automated issue and PR triage.
- See [Deployment](deployment.md) for production setup.
