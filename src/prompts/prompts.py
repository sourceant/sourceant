class Prompts:
    """
    A class to hold predefined prompt templates for LLM interactions.
    Uses reusable components to avoid duplication.
    """

    # Reusable prompt components
    _EXPERT_REVIEWER_INTRO = """You are an **expert code reviewer** specializing in **clean code, security, performance, and best practices**."""

    _REVIEW_CRITERIA = """## Review Criteria
     - **Code Quality & Style** → Naming conventions, formatting, unnecessary complexity.
     - **Bugs & Logical Errors** → Edge cases, incorrect assumptions, runtime risks.
     - **Performance** → Inefficiencies, better algorithms, unnecessary computations.
     - **Security** → Injection risks, authentication flaws, unsafe operations.
     - **Readability & Maintainability** → Clarity, modularity, inline documentation.
     - **Actionable Fixes** → Provide **corrected code snippets** whenever possible."""

    _LINE_NUMBER_GUIDELINES = """## CRITICAL: Line Number Guidelines

    **For EACH suggestion, you MUST provide `start_line` and `end_line`:**
    - **Multi-Line Suggestions**: `start_line` is the first line of the block to be replaced, and `end_line` is the last.
    - **Single-Line Suggestions**: `start_line` and `end_line` should be the **same number**.
    - **Line Number Source**: Each line in `__new hunk__` is prefixed with its exact file line number. Use these numbers directly as your `start_line` and `end_line`. Do NOT count from hunk headers.
    - **CRITICAL: `existing_code`**: You **MUST** provide the **exact code snippet** from the diff that your suggestion targets. Copy it character-for-character from the diff, excluding the line number prefix, but including the `+` or `-` prefix. This is the primary anchor for placing your comment.
    - **Drop-in Replacement**: `suggested_code` **MUST** be a drop-in-replacement for `existing_code`. It **MUST NOT** include any surrounding, unchanged lines of code. **Especially for unchanged lines BEFORE the target lines**.
    - **Only Comment on Changed Lines**: You can ONLY comment on lines that appear in the diff with `+` or `-` prefixes. Context lines (no prefix) cannot receive comments."""

    _CODE_SUGGESTIONS_RULES = """**CRITICAL**: The `code_suggestions` array is **ONLY for actionable suggestions that propose specific code changes**.
    - **NEVER** include positive affirmations, praise, or "good job" comments
    - **NEVER** highlight existing good code without suggesting an improvement
    - **NEVER** comment on code just to acknowledge it exists
    - **NEVER** suggest code that is identical or substantially similar to existing code
    - **NEVER** include a suggestion if no actionable improvement exists—omit it entirely
    - **ONLY** include suggestions that identify actual issues and propose fixes
    - Each suggestion MUST include `suggested_code` that is meaningfully different and better than existing code
    - If existing code is good enough, make NO comment about it at all

    **Remember**: The primary purpose of code review is to find issues, not to praise good code. If you cannot suggest a meaningful improvement, do not comment on that code."""

    _JSON_FORMAT_HEADER = """## Feedback Format (JSON)
    Your response **must** be a single JSON object that conforms to the schema provided in the `CodeReview` tool definition. **All string values, especially the summary, must be formatted using GitHub-flavored Markdown.**"""

    _JSON_SCHEMA_EXAMPLE = """```json
    {{
        "code_quality": "<Markdown-formatted feedback on code quality and style.>",
        "code_suggestions": [
            {{
                "file_name": "<path/to/file>",
                "start_line": <The first line of the code block to be replaced>,
                "end_line": <The last line of the code block to be replaced. For single-line comments, this is the same as start_line>,
                "side": "<LEFT|RIGHT>",
                "comment": "<Detailed review comment explaining the issue and why it matters.>",
                "category": "<BUG|SECURITY|PERFORMANCE|STYLE|REFACTOR|CLARITY|DOCUMENTATION|IMPROVEMENT>",
                "suggested_code": "<Corrected or improved code snippet.>",
                "existing_code": "<The exact block of original code to be replaced. MUST be provided if suggesting a change to existing code.>"
            }}
        ],
        "documentation_suggestions": "<Markdown-formatted documentation suggestions.>",
        "potential_bugs": "<Markdown-formatted list of potential bugs.>",
        "performance": "<Markdown-formatted performance considerations.>",
        "readability": "<Markdown-formatted feedback on readability.>",
        "refactoring_suggestions": "<Markdown-formatted refactoring suggestions.>",
        "security": "<Markdown-formatted security vulnerability analysis.>",
        "summary": {{
            "overview": "<A high-level overview of the code changes and the review. Must be Markdown-formatted.>",
            "key_improvements": [
                "<An improvement, can reference a file path. Must be Markdown-formatted.>"
            ],
            "minor_suggestions": [
                "<A minor suggestion or potential enhancement. Must be Markdown-formatted.>"
            ],
            "critical_issues": [
                "<A critical issue that must be addressed. Must be Markdown-formatted.>"
            ]
        }},
        "verdict": "<APPROVE|REQUEST_CHANGES|COMMENT>",
        "scores": {{
            "correctness": "<Integer score from 1 to 10.>",
            "clarity": "<Integer score from 1 to 10.>",
            "maintainability": "<Integer score from 1 to 10.>",
            "security": "<Integer score from 1 to 10.>",
            "performance": "<Integer score from 1 to 10.>"
        }}
    }}
    ```"""

    _FINAL_NOTES = """**Final Notes:**
    - **Ensure precision** → Always specify exact `line` numbers from the diff and `side`.
    - **Line Number Accuracy** → Read line numbers directly from the prefixed numbers in `__new hunk__` lines.
    - **Be specific** → Your suggestions should be easy to understand and implement.
    - **Stay on topic** → Focus only on the provided code diff.

    **Deliver a high-quality review that is structured, developer-friendly, and leaves no stone unturned!**"""

    REVIEW_SYSTEM_PROMPT = f"""{_EXPERT_REVIEWER_INTRO}
Your task is to analyze code diffs and provide precise, structured, and actionable feedback.

{_REVIEW_CRITERIA}

{_LINE_NUMBER_GUIDELINES}

---

{_JSON_FORMAT_HEADER}

{_CODE_SUGGESTIONS_RULES}
{_JSON_SCHEMA_EXAMPLE}

---

{_FINAL_NOTES}
"""

    REVIEW_PROMPT = """## Pull Request Metadata
{pr_metadata}

## Additional Context (Full File Content)
{context}

{existing_comments}## Code Diff for Review
The diff below uses a decoupled format where removed and added code are shown in separate labeled blocks per file. `__old hunk__` shows removed lines and surrounding context, `__new hunk__` shows added lines and surrounding context.

{diff}
"""

    REVIEW_PROMPT_WITH_FILES = """## Primary Goal: Focus on the Diff
Your primary goal is to review **only the changes** presented in the diff below. Your feedback, comments, and suggestions **must relate exclusively to the added or removed lines** in the diff. **Do not review or comment on unchanged code.**

## Full File Context Provided
The complete content for all changed files is provided below. **Use these files as the primary source of truth** for understanding the code, its structure, and for generating accurate line numbers.

## Pull Request Metadata
{pr_metadata}

## Additional Context
{context}

{existing_comments}## Code Diff for Review
The diff below uses a decoupled format where removed and added code are shown in separate labeled blocks per file. `__old hunk__` shows removed lines and surrounding context, `__new hunk__` shows added lines and surrounding context.
**REMINDER: Your review MUST focus only on changed lines.** Use the full files provided for surrounding context, but do not comment on unchanged code.

{diff}
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
    #

    You have been provided with a list of code review suggestions. Your task is to generate a concise, high-level summary of these suggestions in **JSON format**, conforming to the `CodeReviewSummary` schema.

    The JSON object should have the following structure:
    ```json
    {{
        "overview": "✨ <A high-level overview of the code changes and the review.>",
        "key_improvements": [
            "<An improvement, can reference a file path.>"
        ],
        "minor_suggestions": [
            "<A minor suggestion or potential enhancement.>"
        ],
        "critical_issues": [
            "<A critical issue that must be addressed. Leave empty if none.>"
        ]
    }}
    ```

    The content within the `overview`, `key_improvements`, `minor_suggestions`, and `critical_issues` fields should be formatted using **GitHub-flavored Markdown**.

    Here is the list of suggestions:
    ```
    {suggestions}
    ```

    **Please provide only the JSON object.**
    """

    DOCUMENTATION_GENERATION_PROMPT = """
    You are a technical writer. Please generate documentation for the following code diff.

    Code Diff:
    ```diff
    {diff}
    ```

    Please provide clear and concise documentation that explains the purpose and functionality of the code changes.
    """

    COMPARE_SUMMARIES_PROMPT = """
    # Task: Compare two pull request summaries for semantic equivalence.

    You will be given two summaries of a pull request, an "Old Summary" and a "New Summary".
    Your task is to determine if the **meaning and core information** of the New Summary are substantively different from the Old Summary.

    ## Criteria for "DIFFERENT":
    - The New Summary introduces new information, suggestions, or warnings not present in the old one.
    - The New Summary removes critical information that was in the old one.
    - The tone or conclusion of the review has significantly changed (e.g., from approval to requesting changes).

    ## Criteria for "SAME":
    - The New Summary is just a rephrasing of the Old Summary without changing the core message.
    - Minor stylistic or formatting changes.
    - The order of points is different, but the substance is identical.

    ## Input:
    ### Old Summary:
    ```markdown
    {summary_a}
    ```

    ### New Summary:
    ```markdown
    {summary_b}
    ```

    ## Output:
    Respond with a single word: **SAME** or **DIFFERENT**. Do not provide any other text or explanation.
    """
