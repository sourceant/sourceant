# SourceAnt 🚀

**SourceAnt** is an open-source tool that automates code reviews by integrating GitHub webhooks with the AI-model APIs. It listens for pull request events, analyzes code changes and posts review feedback as comments on GitHub pull requests.

---

## Features ✨
- **Automated Code Reviews**: Analyze pull requests automatically using the DeepSeek API.
- **GitHub Integration**: Seamlessly integrates with GitHub webhooks.
- **Customizable Feedback**: Post detailed, actionable feedback on pull requests.
- **Open Source**: Fully open-source and community-driven.

---

## Getting Started 🛠️

### Prerequisites
- Python 3.8+
- GitHub account with a repository for testing.
- DeepSeek or OpenAI API key (sign up [here](#)). *(Replace with actual link once available)*
- GitHub personal access token with `repo` scope.

### Installation
1. **Clone the Repository**:
   ```bash
   git clone https://github.com/sourceant/sourceant.git
   cd sourceant
   ```

2. **Set Up a Virtual Environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Set Environment Variables**:
   Create a `.env` file in the root directory and add the following:
   ```env
   GITHUB_WEBHOOK_SECRET=your_github_webhook_secret
   DEEPSEEK_API_KEY=your_deepseek_api_key
   GITHUB_TOKEN=your_github_personal_access_token
   ```

5. **Run the Flask Server**:
   ```bash
   python app.py
   ```

---

## Setting Up GitHub Webhook
1. Go to your GitHub repository.
2. Navigate to **Settings > Webhooks > Add Webhook**.
3. Set the **Payload URL** to your server's `/webhook` endpoint (e.g., `https://your-server.com/webhook`).
4. Set the **Content type** to `application/json`.
5. Add the `GITHUB_WEBHOOK_SECRET` to the **Secret** field.
6. Select **Let me select individual events** and choose **Pull requests**.
7. Save the webhook.

---

## How It Works 🧠
1. When a pull request is opened or updated, GitHub sends a webhook payload to SourceAnt.
2. SourceAnt fetches the diff of the pull request using the GitHub API.
3. The diff is sent to the DeepSeek API for analysis.
4. SourceAnt posts the feedback as a comment on the pull request.

---

## Example Workflow
1. Open a pull request in your repository.
2. SourceAnt automatically analyzes the code changes.
3. Feedback is posted as a comment on the pull request:
   ```
   **SourceAnt Code Review:**

   - Consider refactoring this function for better readability.
   - Add error handling for edge cases.
   ```

---

## Contributing 🤝
We welcome contributions! Here’s how you can help:
1. Fork the repository.
2. Create a new branch: `git checkout -b feature/your-feature`.
3. Make your changes and commit them: `git commit -m 'Add some feature'`.
4. Push to the branch: `git push origin feature/your-feature`.
5. Submit a pull request.

Please read our [Contributing Guidelines](CONTRIBUTING.md) for more details.

---

## Roadmap 🗺️
- [x] Set up Flask server and GitHub webhook integration.
- [x] Implement API/Interface to integrate various AI models
- [x] Integrate DeepSeek API for code analysis.
- [ ] Add support for multiple programming languages.
- [ ] Implement a dashboard for review history and metrics.
- [ ] Add CI/CD pipeline for automated testing and deployment.

---

## License 📜
This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

## Contact 📧
Have questions or suggestions? Reach out to us:
- **Email**: opensource@nfebe.com
- **GitHub Issues**: [Open an Issue](https://github.com/sourceant/sourceant/issues)

---

## Contributors ✨
Thanks to these amazing people who have contributed to this project:

<a href="https://github.com/your-username/sourceant/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=sourceant/sourceant" />
</a>

---

Made with ❤️ by [nfebe](https://github.com/nfebe).

