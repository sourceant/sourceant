class Prompts:
    """
    A class to hold predefined prompt templates for LLM interactions.
    Uses reusable components to avoid duplication.
    """

    # Reusable prompt components
    _EXPERT_REVIEWER_INTRO = """You are an **expert code reviewer** specializing in **clean code, security, performance, and best practices**."""

    _REVIEW_CRITERIA = """## 🔍 **Review Criteria**
     - **Code Quality & Style** → Naming conventions, formatting, unnecessary complexity.
     - **Bugs & Logical Errors** → Edge cases, incorrect assumptions, runtime risks.
     - **Performance** → Inefficiencies, better algorithms, unnecessary computations.
     - **Security** → Injection risks, authentication flaws, unsafe operations.
     - **Readability & Maintainability** → Clarity, modularity, inline documentation.
     - **Actionable Fixes** → Provide **corrected code snippets** whenever possible."""

    _LINE_NUMBER_GUIDELINES = """## 📍 **CRITICAL: Line Number Guidelines**

    **For EACH suggestion, you MUST provide `start_line` and `end_line`:**
    - **Multi-Line Suggestions**: `start_line` is the first line of the block to be replaced, and `end_line` is the last.
    - **Single-Line Suggestions**: `start_line` and `end_line` should be the **same number**.
    - **Line Number Source**: Use the line number from the **RIGHT** side of the diff for new/modified code (+ lines) and the **LEFT** side for deleted code (- lines).
    - **`existing_code`**: For each suggestion, provide the **exact block of original code** that your `suggested_code` is meant to replace. This is crucial for accurately placing the comment if line numbers have shifted. For new code additions, this field should be `null`.
    - **Drop-in Replacement**: `suggested_code` **MUST** be a drop-in-replacement for `existing_code`. It **MUST NOT** include any surrounding, unchanged lines of code. **Especially for unchanged lines BEFORE the target lines**."""

    _CODE_SUGGESTIONS_RULES = """**CRITICAL**: The `code_suggestions` array is **ONLY for actionable suggestions that propose specific code changes**. 
    - ❌ **NEVER** include positive affirmations, praise, or "good job" comments
    - ❌ **NEVER** highlight existing good code without suggesting an improvement
    - ❌ **NEVER** comment on code just to acknowledge it exists
    - ❌ **NEVER** suggest code that is identical or substantially similar to existing code
    - ✅ **ONLY** include suggestions that identify actual issues and propose fixes
    - ✅ Each suggestion MUST include `suggested_code` that is meaningfully different and better than existing code
    - ✅ If existing code is good enough, make NO comment about it at all
    
    **Remember**: The primary purpose of code review is to find issues, not to praise good code. If you cannot suggest a meaningful improvement, do not comment on that code."""

    _JSON_FORMAT_HEADER = """## 📝 **Feedback Format (JSON)**
    Your response **must** be a single JSON object that conforms to the schema provided in the `CodeReview` tool definition. **All string values, especially the summary, must be formatted using GitHub-flavored Markdown.**"""

    _JSON_SCHEMA_EXAMPLE = """```json
    {{
        "code_quality": "<Markdown-formatted feedback on code quality and style.>",
        "code_suggestions": [
            {{
                "file_name": "<path/to/file>",
                "start_line": <The first line of the code block to be replaced>,
                "end_line": <The last line of the code block to be replaced. For single-line comments, this is the same as start_line>,
                "position": <position_in_diff>,
                "side": "<LEFT|RIGHT>",
                "comment": "<Detailed review comment explaining the issue and why it matters.>",
                "category": "<bug|security|performance|style|maintainability>",
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
            "overview": "✨ <A high-level overview of the code changes and the review. Must be Markdown-formatted.>",
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

    _FINAL_NOTES = """📢 **Final Notes:**
    - **Ensure precision** → Always specify exact `line` numbers from the diff, `position`, and `side`.
    - **Line Number Accuracy** → Count line numbers from hunk headers (@@ -start,count +start,count @@).
    - **Be specific** → Your suggestions should be easy to understand and implement.
    - **Stay on topic** → Focus only on the provided code diff.

    🚀 **Deliver a high-quality review that is structured, developer-friendly, and leaves no stone unturned!**"""

    REVIEW_PROMPT = f"""
    # 📌 **Comprehensive Code Review Request**

    {_EXPERT_REVIEWER_INTRO}
    Your task is to **analyze the code diff**, provide **precise, structured, and actionable feedback**.

    ## 💬 **How to Use Provided Context**
    You have been given two pieces of information:
    1.  **The Diff**: To show you the specific lines that were changed.
    2.  **Full File Content**: Provided below under "Additional Context".

    **Use the diff to understand the developer's intent and focus your review, but use the full file content as the absolute source of truth for code structure, line numbers, and surrounding logic.** This will prevent any confusion about duplicated code.

    ## 📚 **Additional Context (Full File Content)**
    {{context}}

    {_REVIEW_CRITERIA}

    {_LINE_NUMBER_GUIDELINES}

    --- 

    {_JSON_FORMAT_HEADER}

    {_CODE_SUGGESTIONS_RULES}
    {_JSON_SCHEMA_EXAMPLE}

    ---

    ## 🎯 **Code Diff for Review**
    This diff shows the specific changes to review.
    ```diff
    {{diff}}
    ```

    ---

    {_FINAL_NOTES}
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

    REVIEW_PROMPT_WITH_FILES = f"""
    # 📌 **Comprehensive Code Review Request (with Full File Context)**

    {_EXPERT_REVIEWER_INTRO}
    Your task is to **analyze the code diff**, using the **full file contents provided** to understand the complete context. Your review should be **precise, structured, and actionable**.

    ## 🎯 **Primary Goal: Focus on the Diff**
    Your primary goal is to review **only the changes** presented in the diff below. This is your most important instruction. Your feedback, comments, and suggestions **must relate *exclusively* to the added (`+`) or removed (`-`) lines** in the diff. **Under no circumstances should you review or comment on any code that was not changed.** Reviewing unchanged code is a critical failure and will render your entire review invalid.

    ## 📚 **IMPORTANT: Full File Context Provided**
    The complete content for all changed files has been uploaded and is available to you. **You MUST use these files as the primary source of truth** for understanding the code, its structure, and for generating accurate line numbers.

    ## 💬 **Additional Context**
    {{context}}

    {_REVIEW_CRITERIA}

    ## 📍 **CRITICAL: Line Number Guidelines**

    **For EACH suggestion, you MUST provide `start_line` and `end_line`:**
    - **Source of Truth**: Your primary reference for line numbers is the **full file content** provided, not just the diff.
    - **Multi-Line Suggestions**: `start_line` is the first line of the block to be replaced, and `end_line` is the last.
    - **Single-Line Suggestions**: `start_line` and `end_line` should be the **same number**.
    - **Line Number Source**: Use the line number from the **RIGHT** side of the diff for new/modified code (+ lines) and the **LEFT** side for deleted code (- lines).
    - **`existing_code`**: For each suggestion, provide the **exact block of original code** that your `suggested_code` is meant to replace. This is crucial for accurately placing the comment. For new code additions, this field should be `null`.
    - **Drop-in Replacement**: `suggested_code` **MUST** be a drop-in replacement for `existing_code`. It **MUST NOT** include any surrounding, unchanged lines of code. **Especially for unchanged lines BEFORE the target lines**.

    --- 

    {_JSON_FORMAT_HEADER}

    {_CODE_SUGGESTIONS_RULES}
    --- 

    ## 🎯 **Code Diff for Review**
    This diff shows the specific changes to review. **REMINDER: Your review MUST focus *only* on the lines prefixed with `+` or `-` below.** Use the full files provided for the surrounding context, but do not comment on unchanged code.
    ```diff
    {{diff}}
    ```

    --- 

    🚀 **Deliver a high-quality review that is structured, developer-friendly, and leverages the full context of the provided files.**
    """
