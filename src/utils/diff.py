import requests
import os
from src.models.repository_event import RepositoryEvent
from src.utils.logger import logger
from typing import Optional


def handle_github_api_error(response: requests.Response, context: str) -> None:
    logger.error(
        f"GitHub API Error {context}: {response.status_code} - {response.text}"
    )
    return None


def get_diff_between_shas(
    repo_full_name: str, base_sha: str, head_sha: str, headers: dict
) -> Optional[str]:
    compare_url = (
        f"https://api.github.com/repos/{repo_full_name}/compare/{base_sha}...{head_sha}"
    )
    logger.debug(f"Fetching diff from: {compare_url}")
    try:
        diff_headers = headers
        diff_headers["Accept"] = "application/vnd.github.v3.diff"
        response = requests.get(compare_url, headers=diff_headers)
        response.raise_for_status()
        logger.info("Diff fetched successfully.")
        return response.text
    except requests.exceptions.HTTPError:
        return handle_github_api_error(response, "fetching diff")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching diff: {e}")
        return None
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        return None


def get_diff_from_push(
    repo_full_name: str, base_ref: str, after_sha: str, headers: dict
) -> Optional[str]:
    return get_diff_between_shas(repo_full_name, base_ref, after_sha, headers)


def get_diff_from_pr(
    repo_full_name: str, pr_number: int, headers: dict
) -> Optional[str]:
    pr_url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}"
    logger.debug(f"Fetching PR info from: {pr_url}")
    try:
        pr_headers = headers.copy()
        pr_headers["Accept"] = "application/vnd.github.v3.json"
        pr_response = requests.get(pr_url, headers=pr_headers)
        pr_response.raise_for_status()
        pr_data = pr_response.json()

        diff_url = pr_data.get("diff_url")
        if diff_url:
            logger.debug(f"Fetching diff from: {diff_url}")
            diff_headers = headers.copy()
            diff_headers["Accept"] = "application/vnd.github.v3.diff"
            diff_response = requests.get(diff_url, headers=diff_headers)
            diff_response.raise_for_status()
            logger.info("Diff fetched successfully.")
            return diff_response.text
        else:
            logger.error("Diff URL not found in PR response.")
            return None
    except requests.exceptions.HTTPError:
        return handle_github_api_error(
            pr_response, "Failed to get PR info or diff from PR"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Error getting PR info or diff: {e}")
        return None
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        return None


def get_diff(event: RepositoryEvent) -> Optional[str]:
    if event is None:
        logger.error("Event is None. Cannot compute diff.")
        return None
    if not event.payload:
        logger.error("Payload not found in event.")
        return None

    payload = event.payload
    repository = payload.get("repo", {})
    repository_full_name = repository.get("full_name")
    if not repository_full_name or not isinstance(repository_full_name, str):
        logger.error("Repository full name not found in payload.")
        return None
    repo_private = repository.get("private", False)
    headers = {}

    if repo_private:
        github_token = os.environ.get("GITHUB_TOKEN")
        if not github_token:
            logger.error("GITHUB_TOKEN environment variable not set for private repo.")
            return None
        headers["Authorization"] = f"token {github_token}"

    action = payload.get("action")
    if action in ("opened", "synchronize", "reopened"):
        pr_number = payload.get("number")
        if not pr_number:
            logger.error("Pull request number not found in payload.")
            return None
        return get_diff_from_pr(repository.get("full_name"), pr_number, headers)

    after_sha = payload.get("after")
    base_ref = payload.get("base_ref")
    if after_sha and base_ref:
        return get_diff_from_push(
            repository.get("full_name"), base_ref, after_sha, headers
        )

    logger.info("No diff to compute for this event.")
    return None
