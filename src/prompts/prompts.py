class Prompts:
    """
    A class to hold predefined prompt templates for LLM interactions.
    """

    REVIEW_PROMPT = """
    You are an expert code reviewer. Please review the following code diff and provide feedback on:
    - Code quality and style
    - Potential bugs or errors
    - Performance considerations
    - Security vulnerabilities
    - Readability and maintainability
    - Code suggestions covering all of the above
    - Code suggestions must be actionable and specific and include file name and line number in a structured format (json)

    Code Diff:
    ```diff
    {diff}
    ```

    Context (if any):
    {context}

    Please provide a detailed review in a clear and concise manner.
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

    DOCUMENTATION_GENERATION_PROMPT = """
    You are a technical writer. Please generate documentation for the following code diff.

    Code Diff:
    ```diff
    {diff}
    ```

    Please provide clear and concise documentation that explains the purpose and functionality of the code changes.
    """
