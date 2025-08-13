"""
GitHub OAuth App client for making API calls on behalf of users.
"""

import requests
from typing import Dict, Any, Optional
from src.utils.logger import logger


class GitHubOAuthClient:
    """
    GitHub OAuth App client for making API calls.
    
    Uses app credentials to make API calls on behalf of users
    without storing user tokens.
    """
    
    def __init__(self, client_id: str, client_secret: str):
        """
        Initialize OAuth client.
        
        Args:
            client_id: GitHub OAuth app client ID  
            client_secret: GitHub OAuth app client secret
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.github_api_base = "https://api.github.com"
    
    async def get_user_info_from_webhook(self, webhook_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract user information from webhook payload.
        
        Args:
            webhook_payload: GitHub webhook payload
            
        Returns:
            User information dictionary
        """
        try:
            # Extract user info from different webhook types
            user_info = None
            
            if 'sender' in webhook_payload:
                user_info = webhook_payload['sender']
            elif 'pull_request' in webhook_payload and 'user' in webhook_payload['pull_request']:
                user_info = webhook_payload['pull_request']['user']
            
            if user_info:
                return {
                    'github_id': user_info.get('id'),
                    'username': user_info.get('login'), 
                    'avatar_url': user_info.get('avatar_url'),
                    'url': user_info.get('url'),
                    'type': user_info.get('type')
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error extracting user info from webhook: {e}")
            return None
    
    async def get_repository_info_from_webhook(self, webhook_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract repository information from webhook payload.
        
        Args:
            webhook_payload: GitHub webhook payload
            
        Returns:
            Repository information dictionary
        """
        try:
            repo_info = webhook_payload.get('repository')
            if not repo_info:
                return None
            
            return {
                'github_repo_id': repo_info.get('id'),
                'full_name': repo_info.get('full_name'),
                'name': repo_info.get('name'),
                'owner': repo_info.get('owner', {}).get('login'),
                'private': repo_info.get('private', False),
                'clone_url': repo_info.get('clone_url'),
                'ssh_url': repo_info.get('ssh_url'),
                'default_branch': repo_info.get('default_branch')
            }
            
        except Exception as e:
            logger.error(f"Error extracting repository info from webhook: {e}")
            return None
    
    async def get_pull_request_diff(
        self, 
        owner: str, 
        repo: str, 
        pr_number: int
    ) -> Optional[str]:
        """
        Get pull request diff using OAuth app credentials.
        
        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: Pull request number
            
        Returns:
            Diff content as string
        """
        try:
            url = f"{self.github_api_base}/repos/{owner}/{repo}/pulls/{pr_number}"
            
            headers = {
                'Accept': 'application/vnd.github.v3.diff',
                'X-GitHub-Api-Version': '2022-11-28',
                'User-Agent': 'SourceAnt-OAuth-App'
            }
            
            # Use basic auth with OAuth app credentials
            auth = (self.client_id, self.client_secret)
            
            response = requests.get(url, headers=headers, auth=auth, timeout=30)
            response.raise_for_status()
            
            return response.text
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get PR diff for {owner}/{repo}#{pr_number}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting PR diff: {e}")
            return None
    
    async def get_file_content(
        self, 
        owner: str, 
        repo: str, 
        file_path: str, 
        ref: str
    ) -> Optional[str]:
        """
        Get file content from repository.
        
        Args:
            owner: Repository owner
            repo: Repository name
            file_path: Path to file
            ref: Git reference (commit SHA, branch, tag)
            
        Returns:
            File content as string
        """
        try:
            url = f"{self.github_api_base}/repos/{owner}/{repo}/contents/{file_path}"
            
            headers = {
                'Accept': 'application/vnd.github.v3+json',
                'X-GitHub-Api-Version': '2022-11-28',
                'User-Agent': 'SourceAnt-OAuth-App'
            }
            
            params = {'ref': ref}
            auth = (self.client_id, self.client_secret)
            
            response = requests.get(url, headers=headers, auth=auth, params=params, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            
            # Decode base64 content
            import base64
            if 'content' in data:
                content = base64.b64decode(data['content']).decode('utf-8')
                return content
            
            return None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get file content for {owner}/{repo}/{file_path}@{ref}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting file content: {e}")
            return None
    
    async def post_review_comment(
        self, 
        owner: str, 
        repo: str, 
        pr_number: int,
        body: str,
        commit_sha: str,
        path: str,
        position: Optional[int] = None,
        line: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Post a review comment on a pull request.
        
        Args:
            owner: Repository owner
            repo: Repository name  
            pr_number: Pull request number
            body: Comment body
            commit_sha: SHA of the commit
            path: File path
            position: Position in diff (for diff-based comments)
            line: Line number (for line-based comments)
            
        Returns:
            Comment response or None if failed
        """
        try:
            url = f"{self.github_api_base}/repos/{owner}/{repo}/pulls/{pr_number}/comments"
            
            headers = {
                'Accept': 'application/vnd.github.v3+json',
                'X-GitHub-Api-Version': '2022-11-28',
                'User-Agent': 'SourceAnt-OAuth-App',
                'Content-Type': 'application/json'
            }
            
            comment_data = {
                'body': body,
                'commit_id': commit_sha,
                'path': path
            }
            
            if position is not None:
                comment_data['position'] = position
            elif line is not None:
                comment_data['line'] = line
                comment_data['side'] = 'RIGHT'
            
            auth = (self.client_id, self.client_secret)
            
            response = requests.post(
                url, 
                headers=headers, 
                auth=auth, 
                json=comment_data, 
                timeout=30
            )
            response.raise_for_status()
            
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to post review comment: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error posting review comment: {e}")
            return None
    
    async def create_pull_request_review(
        self,
        owner: str,
        repo: str, 
        pr_number: int,
        body: str,
        event: str = "COMMENT",
        comments: Optional[list] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create a pull request review.
        
        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: Pull request number
            body: Review body
            event: Review event (COMMENT, APPROVE, REQUEST_CHANGES)
            comments: List of review comments
            
        Returns:
            Review response or None if failed
        """
        try:
            url = f"{self.github_api_base}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
            
            headers = {
                'Accept': 'application/vnd.github.v3+json',
                'X-GitHub-Api-Version': '2022-11-28',
                'User-Agent': 'SourceAnt-OAuth-App',
                'Content-Type': 'application/json'
            }
            
            review_data = {
                'body': body,
                'event': event
            }
            
            if comments:
                review_data['comments'] = comments
            
            auth = (self.client_id, self.client_secret)
            
            response = requests.post(
                url,
                headers=headers,
                auth=auth,
                json=review_data,
                timeout=30
            )
            response.raise_for_status()
            
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to create PR review: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error creating PR review: {e}")
            return None