"""
Prompt templates for the Repo Manager plugin.
"""


class RepoManagerPrompts:
    PR_DEDUP_PROMPT = """You are a repository management assistant. A new pull request has been opened. Your job is to check if any existing open pull requests are duplicates or very closely related.

**New Pull Request:**
- Title: {new_title}
- Body: {new_body}

**Existing Open Pull Requests:**
{existing_prs}

Analyze the new PR against the existing ones. Two PRs are duplicates if they:
- Address the same issue or feature
- Make very similar code changes
- Have substantially overlapping goals

Return a JSON array of duplicate PR numbers. If no duplicates are found, return an empty array.
Return ONLY the JSON array, no other text.

Example response: [42, 15]
Example response (no duplicates): []"""

    ISSUE_DEDUP_PROMPT = """You are a repository management assistant. A new issue has been opened. Your job is to check if any existing open issues are duplicates or very closely related.

**New Issue:**
- Title: {new_title}
- Body: {new_body}

**Existing Open Issues:**
{existing_issues}

Analyze the new issue against the existing ones. Two issues are duplicates if they:
- Report the same bug or problem
- Request the same feature or enhancement
- Describe substantially the same concern

Return a JSON array of duplicate issue numbers. If no duplicates are found, return an empty array.
Return ONLY the JSON array, no other text.

Example response: [42, 15]
Example response (no duplicates): []"""

    AUTO_LABEL_PROMPT = """You are a repository management assistant. Based on the following content, suggest appropriate labels from the available label set.

**Title:** {title}
**Body:** {body}
{diff_section}

**Available Labels:**
{available_labels}

Select the most appropriate labels from the available set above. Only suggest labels that exist in the available set.
Return a JSON array of label names. If no labels are appropriate, return an empty array.
Return ONLY the JSON array, no other text.

Example response: ["bug", "documentation"]
Example response (no labels): []"""
