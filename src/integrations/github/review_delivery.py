import hashlib
import json
import re
from urllib.parse import quote

import requests


def retry_after(response):
    if response is None:
        return None
    body = response.text.lower()
    if response.status_code != 429 and (
        response.status_code not in (403, 422)
        or not any(
            word in body for word in ("rate limit", "submitted too quickly", "abuse")
        )
    ):
        return None
    try:
        return max(60, int(response.headers.get("Retry-After", "60")))
    except ValueError:
        return 60


def marker_for(repository, pull_request, review):
    content = json.dumps(review.model_dump(mode="json"), sort_keys=True)
    key = f"{repository.owner}/{repository.name}:{pull_request.number}:{pull_request.head_sha}:{content}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def existing(url, headers, marker):
    while url:
        response = requests.get(
            url, headers=headers, params={"per_page": 100}, timeout=30
        )
        response.raise_for_status()
        for item in response.json():
            if item.get("user", {}).get("type") == "Bot" and marker in (
                item.get("body") or ""
            ):
                return item
        url = response.links.get("next", {}).get("url")
    return None


def comment_for(suggestion, mapper):
    path = suggestion.file_name
    if path.startswith(("a/", "b/")):
        path = path[2:]
    parsed = mapper.file_map.get(path)
    side = suggestion.side.value if suggestion.side else "RIGHT"
    end = suggestion.end_line
    start = suggestion.start_line or end
    if not parsed or not start or not end or start > end:
        return None
    if (start, side) not in parsed.line_to_position or (
        end,
        side,
    ) not in parsed.line_to_position:
        return None
    ranges = [(a, b) if side == "LEFT" else (c, d) for a, b, c, d in parsed.hunk_ranges]
    if not any(a <= start <= end <= b for a, b in ranges):
        return None
    body = suggestion.comment or ""
    if suggestion.suggested_code:
        body += f"\n\n```suggestion\n{suggestion.suggested_code}\n```"
    comment = {"path": path, "body": body, "line": end, "side": side}
    if start < end:
        comment.update(start_line=start, start_side=side)
    return comment


def finding_sections(repository, pull_request, review):
    sections = []
    for suggestion in review.code_suggestions or ():
        path = suggestion.file_name or "Unknown file"
        if path.startswith(("a/", "b/")):
            path = path[2:]
        revision = (
            pull_request.base_sha
            if suggestion.side and suggestion.side.value == "LEFT"
            else pull_request.head_sha
        )
        link = f"https://github.com/{repository.owner}/{repository.name}/blob/{revision}/{quote(path, safe='/')}"
        if (
            suggestion.end_line
            and 0
            < (suggestion.start_line or suggestion.end_line)
            <= suggestion.end_line
        ):
            link += f"#L{suggestion.start_line or suggestion.end_line}-L{suggestion.end_line}"
        section = f"### [{path}]({link})\n\n{suggestion.comment or ''}\n"
        if suggestion.suggested_code:
            longest = max(
                (len(run) for run in re.findall(r"`+", suggestion.suggested_code)),
                default=0,
            )
            fence = "`" * max(3, longest + 1)
            section += f"\nSuggested replacement:\n{fence}\n{suggestion.suggested_code}\n{fence}\n"
        sections.append(section)
    if review.summary:
        model_findings = {one.comment for one in review.code_suggestions or ()}
        sections.extend(
            f"{text}\n"
            for text in [
                *review.summary.critical_issues,
                *review.summary.minor_suggestions,
            ]
            if text not in model_findings
        )
    return sections


def post_findings(repository, pull_request, review, headers, marker):
    url = f"https://api.github.com/repos/{repository.owner}/{repository.name}/issues/{pull_request.number}/comments"
    sections = finding_sections(repository, pull_request, review)
    parts, current = [], ""
    for section in sections:
        if current and len(current) + len(section) > 45000:
            parts.append(current)
            current = ""
        while section:
            room = 45000 - len(current)
            current += section[:room]
            section = section[room:]
            if len(current) >= 45000:
                parts.append(current)
                current = ""
    if current or not parts:
        parts.append(current)
    ids = []
    for index, part in enumerate(parts):
        tag = f"<!-- SOURCEANT_FINDINGS:{marker}:{index} -->"
        found = existing(url, headers, tag)
        if found:
            ids.append(found["id"])
            continue
        body = (
            f"## Review findings ({index + 1}/{len(parts)})\n\n"
            "GitHub could not accept this review as a native pull request review. "
            "The findings are posted here instead. This comment does not submit "
            "an approval or a changes-requested review.\n\n"
            f"{part}\n\nVerdict: {review.verdict.value}\n\n{tag}"
        )
        response = requests.post(url, headers=headers, json={"body": body}, timeout=30)
        response.raise_for_status()
        ids.append(response.json()["id"])
    return ids


def deliver(
    github,
    repository,
    pull_request,
    review,
    mapper,
    configuration,
    delivery_id=None,
):
    from src.core.settings.configuration import Configuration

    configuration = configuration or Configuration(repository=repository.full_name)
    marker = delivery_id or marker_for(repository, pull_request, review)
    tag = f"<!-- SOURCEANT_REVIEW:{marker} -->"
    root = f"https://api.github.com/repos/{repository.owner}/{repository.name}"
    try:
        token = github.get_installation_access_token(repository.owner, repository.name)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        found = existing(f"{root}/pulls/{pull_request.number}/reviews", headers, tag)
        fallback = existing(
            f"{root}/issues/{pull_request.number}/comments",
            headers,
            f"<!-- SOURCEANT_FINDINGS:{marker}:0 -->",
        )
        comments = [comment_for(one, mapper) for one in review.code_suggestions or ()]
        model_findings = {one.comment for one in review.code_suggestions or ()}
        tool_findings = (
            [
                text
                for text in [
                    *review.summary.critical_issues,
                    *review.summary.minor_suggestions,
                ]
                if text not in model_findings
            ]
            if review.summary
            else []
        )
        unanchored = review.model_copy(
            update={
                "code_suggestions": [
                    one
                    for one, comment in zip(review.code_suggestions or (), comments)
                    if comment is None
                ],
                "summary": None,
            }
        )
        body = (
            "\n".join(
                finding_sections(repository, pull_request, unanchored) + tool_findings
            )
            or "Review complete. See the overview comment for a summary."
        )
        comments = [comment for comment in comments if comment is not None]
        needs_fallback = not found and fallback is not None
        if (
            not found
            and not needs_fallback
            and (
                comments
                or unanchored.code_suggestions
                or tool_findings
                or review.verdict.value != "COMMENT"
            )
        ):
            attempts = [(comments, body)]
            if comments:
                attempts.append(
                    ([], "\n".join(finding_sections(repository, pull_request, review)))
                )
            for inline, review_body in attempts:
                try:
                    response = requests.post(
                        f"{root}/pulls/{pull_request.number}/reviews",
                        headers=headers,
                        json={
                            "commit_id": pull_request.head_sha,
                            "event": review.verdict.value,
                            "body": f"{review_body}\n\n{tag}",
                            "comments": inline,
                        },
                        timeout=60,
                    )
                    response.raise_for_status()
                    break
                except requests.HTTPError as error:
                    if (
                        retry_after(error.response) is not None
                        or error.response is None
                        or error.response.status_code != 422
                    ):
                        raise
            else:
                needs_fallback = True
        if needs_fallback:
            post_findings(repository, pull_request, review, headers, marker)
        if review.summary:
            github._create_or_update_overview_comment(
                repository.owner,
                repository.name,
                pull_request.number,
                github._format_summary(review.summary, configuration),
                headers,
            )
        return {
            "status": "partial_success" if needs_fallback else "success",
            "message": "Review findings delivered",
        }
    except requests.RequestException as error:
        delay = retry_after(error.response)
        transient = error.response is None or error.response.status_code >= 500
        return {
            "status": "pending" if delay is not None or transient else "error",
            "retry_after": delay or 60,
            "message": str(error),
        }
    except ValueError as error:
        return {"status": "error", "message": str(error)}
