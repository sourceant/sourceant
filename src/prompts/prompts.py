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

    ## 📝 **Feedback Format (GitHub-Compatible JSON)**  
    Your response for suggested_code fields **must** follow the JSON schema below, ensuring precise issue tracking:  

    ```json
    [
        {
            "file": "<file_path>",
            "line": <line_number>,  // For single-line comments  
            "start_line": <start_line_number>,  // For multi-line comments  
            "position": <position_in_diff>,  
            "side": "<left|right>",  
            "comment": "<detailed_review_comment>",  
            "suggested_code": "<corrected_or_improved_code>"  
        }
    ]
    ```

    - **`file`** → The exact file where the issue exists.  
    - **`line`** → The **single** affected line number (if applicable).  
    - **`start_line`** → The **starting line number** for multi-line comments (if applicable).  
    - **`position`** → The index of the change in the diff.  
    - **`side`** → Whether the comment applies to the **left (before)** or **right (after)** side of the diff.  
    - **`comment`** → A **clear, constructive** explanation of the issue.  
    - **`suggested_code`** → A **corrected version** of the problematic code.  

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

    DOCUMENTATION_GENERATION_PROMPT = """
    You are a technical writer. Please generate documentation for the following code diff.

    Code Diff:
    ```diff
    {diff}
    ```

    Please provide clear and concise documentation that explains the purpose and functionality of the code changes.
    """
