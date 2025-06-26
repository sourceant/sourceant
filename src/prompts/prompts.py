class Prompts:
    """
    A class to hold predefined prompt templates for LLM interactions.
    """

    """
    A class to hold predefined prompt templates for LLM interactions.
    """

    REVIEW_PROMPT = """
    # 📌 **Comprehensive Code Review Request**

    You are an **expert code reviewer** specializing in **clean code, security, performance, and best practices**.
    Your task is to **analyze the code diff** and provide **precise, structured, and actionable feedback**.

    ## 🔍 **Review Criteria**
     - **Code Quality & Style** → Naming conventions, formatting, unnecessary complexity.
     - **Bugs & Logical Errors** → Edge cases, incorrect assumptions, runtime risks.
     - **Performance** → Inefficiencies, better algorithms, unnecessary computations.
     - **Security** → Injection risks, authentication flaws, unsafe operations.
     - **Readability & Maintainability** → Clarity, modularity, inline documentation.
     - **Actionable Fixes** → Provide **corrected code snippets** whenever possible.

    ---

    ## 📝 **Feedback Format (JSON)**
    Your response **must** be a single JSON object that conforms to the schema below.
    **All string values, especially the summary, must be formatted using GitHub-flavored Markdown.**

    ```json
    {{
        "code_quality": "<Markdown-formatted feedback on code quality and style.>",
        "code_suggestions": [
            {{
                "file_name": "<path/to/file>",
                "line": <line_number>,
                "start_line": <start_line_number>,
                "position": <position_in_diff>,
                "side": "<LEFT|RIGHT>",
                "comment": "<Detailed review comment.>",
                "suggested_code": "<Corrected or improved code.>"
            }}
        ],
        "documentation_suggestions": "<Markdown-formatted documentation suggestions.>",
        "potential_bugs": "<Markdown-formatted list of potential bugs.>",
        "performance": "<Markdown-formatted performance considerations.>",
        "readability": "<Markdown-formatted feedback on readability.>",
        "refactoring_suggestions": "<Markdown-formatted refactoring suggestions.>",
        "security": "<Markdown-formatted security vulnerability analysis.>",
        "summary": "<**Overall summary in Markdown.** Use headings, lists, and bold text for clarity.>",
        "verdict": "<APPROVE|REQUEST_CHANGES|COMMENT>"
    }}
    ```

    ---

    ## 🎯 **Code Diff for Review**
    ```diff
    {diff}
    ```

    ## 📝 **Additional Context:**
    {context}

    ---

    📢 **Final Notes:**
    - **Ensure precision** → Always specify `file`, `line`, `position`, and `side`.
    - **Structured & Clear** → Use JSON format for easy automation and integration.
    - **Be Constructive & Actionable** → Don't just point out problems—suggest improvements.
    - **Follow Best Practices** → Use **proper coding standards, security guidelines, and optimization techniques**.

    🚀 **Deliver a high-quality review that is structured, developer-friendly, and leaves no stone unturned!**
    """

    SUMMARIZE_PROMPT = """
    Please summarize the following code changes in a few sentences:

    Code Diff:
    ```diff
    {diff}
    ```
    """

    REFACTOR_SUGGESTIONS_PROMPT = """
    You are an expert software engineer. Please review the following code diff and provide suggestions for refactoring to improve code quality, readability, and maintainability.

    Code Diff:
    ```diff
    {diff}
    ```

    Please provide specific refactoring suggestions with code examples where applicable.
    """

    SUMMARIZE_REVIEW_PROMPT = """
    # 📝 **Code Review Summary**

    You have been provided with a list of code review suggestions. Your task is to generate a concise, high-level summary of these suggestions in **GitHub-flavored Markdown**.

    The summary should:
    - Start with a general overview of the changes.
    - Group related suggestions under appropriate headings (e.g., "Bug Fixes", "Style Improvements", "Security Concerns").
    - Be easy to read and understand for the pull request author.
    - Do NOT include the code snippets themselves, only the comments.

    Here is the list of suggestions:
    ```
    {suggestions}
    ```

    **Please provide only the markdown summary.**
    """

    DOCUMENTATION_GENERATION_PROMPT = """
    You are a technical writer. Please generate documentation for the following code diff.

    Code Diff:
    ```diff
    {diff}
    ```

    Please provide clear and concise documentation that explains the purpose and functionality of the code changes.
    """
