"""
GitHub OAuth flow handler for SourceAnt.
"""

import secrets
import base64
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from urllib.parse import urlencode
import requests

from sqlalchemy.orm import Session
from sqlalchemy import or_

from .models import User, OAuthToken, UserRepository, UserSession
from src.config.db import get_db
from src.config.settings import STATELESS_MODE, APP_URL
from src.utils.logger import logger


class GitHubOAuthHandler:
    """
    Handles GitHub OAuth 2.0 flow for user authentication.

    Implements the standard OAuth 2.0 authorization code flow with PKCE
    for secure user authentication and token management.
    """

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        """
        Initialize OAuth handler.

        Args:
            client_id: GitHub OAuth app client ID
            client_secret: GitHub OAuth app client secret
            redirect_uri: Callback URI for OAuth flow
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.github_api_base = "https://api.github.com"
        self.github_oauth_base = "https://github.com/login/oauth"

    def generate_auth_url(
        self, state: Optional[str] = None, scope: str = "read:user,repo"
    ) -> Dict[str, str]:
        """
        Generate GitHub OAuth authorization URL.

        Args:
            state: Optional state parameter for CSRF protection
            scope: OAuth scopes to request

        Returns:
            Dictionary containing auth URL and state
        """
        if state is None:
            state = secrets.token_urlsafe(32)

        # Generate PKCE code challenge
        code_verifier = secrets.token_urlsafe(32)
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )

        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": scope,
            "state": state,
            "response_type": "code",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        auth_url = f"{self.github_oauth_base}/authorize?{urlencode(params)}"

        return {"auth_url": auth_url, "state": state, "code_verifier": code_verifier}

    async def exchange_code_for_token(
        self, code: str, code_verifier: str, state: str
    ) -> Optional[Dict[str, Any]]:
        """
        Exchange authorization code for access token.

        Args:
            code: Authorization code from GitHub
            code_verifier: PKCE code verifier
            state: State parameter for CSRF validation

        Returns:
            Token response dictionary or None if failed
        """
        try:
            token_data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": code_verifier,
            }

            headers = {
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            }

            response = requests.post(
                f"{self.github_oauth_base}/access_token",
                data=token_data,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()

            token_response = response.json()

            if "error" in token_response:
                logger.error(f"OAuth token exchange error: {token_response}")
                return None

            return token_response

        except requests.RequestException as e:
            logger.error(f"Error exchanging code for token: {e}")
            return None

    async def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        """
        Get user information from GitHub API.

        Args:
            access_token: GitHub access token

        Returns:
            User information dictionary or None if failed
        """
        try:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }

            response = requests.get(
                f"{self.github_api_base}/user", headers=headers, timeout=30
            )
            response.raise_for_status()

            user_info = response.json()
            return user_info

        except requests.RequestException as e:
            logger.error(f"Error fetching user info: {e}")
            return None

    async def get_user_repositories(
        self, access_token: str, per_page: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get user's repositories from GitHub API.

        Args:
            access_token: GitHub access token
            per_page: Number of repositories per page

        Returns:
            List of repository information dictionaries
        """
        repositories = []
        page = 1

        try:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }

            while True:
                params = {"sort": "updated", "per_page": per_page, "page": page}

                response = requests.get(
                    f"{self.github_api_base}/user/repos",
                    headers=headers,
                    params=params,
                    timeout=30,
                )
                response.raise_for_status()

                page_repos = response.json()
                if not page_repos:
                    break

                repositories.extend(page_repos)

                # Check if we've reached the last page
                if len(page_repos) < per_page:
                    break

                page += 1

            logger.info(f"Retrieved {len(repositories)} repositories for user")
            return repositories

        except requests.RequestException as e:
            logger.error(f"Error fetching user repositories: {e}")
            return []

    async def create_or_update_user(
        self, user_info: Dict[str, Any], token_info: Dict[str, Any]
    ) -> Optional[User]:
        """
        Create or update user in database.

        Args:
            user_info: User information from GitHub API
            token_info: Token information from OAuth exchange

        Returns:
            User instance or None if failed
        """
        try:
            db: Session = next(get_db())

            github_id = user_info.get("id")
            username = user_info.get("login")

            if not github_id or not username:
                logger.error("Missing required user information from GitHub")
                return None

            # Find existing user or create new one
            user = db.query(User).filter(User.github_id == github_id).first()

            if user:
                # Update existing user
                user.username = username
                user.email = user_info.get("email")
                user.name = user_info.get("name")
                user.avatar_url = user_info.get("avatar_url")
                user.updated_at = datetime.utcnow()
            else:
                # Create new user
                user = User(
                    github_id=github_id,
                    username=username,
                    email=user_info.get("email"),
                    name=user_info.get("name"),
                    avatar_url=user_info.get("avatar_url"),
                )
                db.add(user)

            # Handle OAuth token
            access_token = token_info.get("access_token")
            if access_token:
                # Remove existing tokens for this user
                db.query(OAuthToken).filter(OAuthToken.user_id == user.id).delete()

                # Calculate expiration time
                expires_in = token_info.get("expires_in")
                expires_at = None
                if expires_in:
                    expires_at = datetime.utcnow() + timedelta(seconds=int(expires_in))

                # Create new token
                oauth_token = OAuthToken(
                    user_id=user.id,
                    access_token=access_token,
                    refresh_token=token_info.get("refresh_token"),
                    token_type=token_info.get("token_type", "bearer"),
                    scope=token_info.get("scope"),
                    expires_at=expires_at,
                )
                db.add(oauth_token)

            db.commit()
            db.refresh(user)

            logger.info(f"Created/updated user: {username} (GitHub ID: {github_id})")
            return user

        except Exception as e:
            logger.error(f"Error creating/updating user: {e}")
            if "db" in locals():
                db.rollback()
            return None
        finally:
            if "db" in locals():
                db.close()

    async def update_user_repositories(
        self, user: User, repositories: List[Dict[str, Any]]
    ) -> int:
        """
        Update user's repository list in database.

        Args:
            user: User instance
            repositories: List of repository information from GitHub API

        Returns:
            Number of repositories updated
        """
        try:
            db: Session = next(get_db())
            updated_count = 0

            # Get current repository IDs from GitHub
            github_repo_ids = {repo["id"] for repo in repositories}

            # Remove repositories that no longer exist
            db.query(UserRepository).filter(
                UserRepository.user_id == user.id,
                ~UserRepository.github_repo_id.in_(github_repo_ids),
            ).delete(synchronize_session=False)

            # Update or create repositories
            for repo_info in repositories:
                github_repo_id = repo_info["id"]
                full_name = repo_info["full_name"]
                owner, name = full_name.split("/", 1)

                existing_repo = (
                    db.query(UserRepository)
                    .filter(
                        UserRepository.user_id == user.id,
                        UserRepository.github_repo_id == github_repo_id,
                    )
                    .first()
                )

                if existing_repo:
                    # Update existing repository
                    existing_repo.full_name = full_name
                    existing_repo.owner = owner
                    existing_repo.name = name
                    existing_repo.private = repo_info.get("private", False)
                    existing_repo.updated_at = datetime.utcnow()
                else:
                    # Create new repository
                    user_repo = UserRepository(
                        user_id=user.id,
                        github_repo_id=github_repo_id,
                        full_name=full_name,
                        owner=owner,
                        name=name,
                        private=repo_info.get("private", False),
                    )
                    db.add(user_repo)

                updated_count += 1

            db.commit()
            logger.info(
                f"Updated {updated_count} repositories for user {user.username}"
            )
            return updated_count

        except Exception as e:
            logger.error(f"Error updating user repositories: {e}")
            if "db" in locals():
                db.rollback()
            return 0
        finally:
            if "db" in locals():
                db.close()

    async def get_valid_token(self, user: User) -> Optional[str]:
        """
        Get valid access token for user, refreshing if necessary.

        Args:
            user: User instance

        Returns:
            Valid access token or None if unavailable
        """
        try:
            db: Session = next(get_db())

            # Get the most recent token
            token = (
                db.query(OAuthToken)
                .filter(OAuthToken.user_id == user.id)
                .order_by(OAuthToken.created_at.desc())
                .first()
            )

            if not token:
                return None

            # Check if token is still valid
            if token.expires_at and token.expires_at <= datetime.utcnow():
                logger.info(f"Token expired for user {user.username}")
                db.close()
                return await self.refresh_token(user)

            return token.access_token

        except Exception as e:
            logger.error(f"Error getting valid token: {e}")
            return None
        finally:
            if "db" in locals():
                db.close()

    async def create_user_session(self, user: User, duration_hours: int = 24) -> str:
        """
        Create a user session.

        Args:
            user: User instance
            duration_hours: Session duration in hours

        Returns:
            Session ID
        """
        try:
            db: Session = next(get_db())

            session_id = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(32)
            expires_at = datetime.utcnow() + timedelta(hours=duration_hours)

            # Remove existing sessions for user
            db.query(UserSession).filter(UserSession.user_id == user.id).delete()

            # Create new session
            user_session = UserSession(
                session_id=session_id,
                user_id=user.id,
                csrf_token=csrf_token,
                expires_at=expires_at,
            )
            db.add(user_session)
            db.commit()

            logger.info(f"Created session for user {user.username}")
            return session_id

        except Exception as e:
            logger.error(f"Error creating user session: {e}")
            if "db" in locals():
                db.rollback()
            return ""
        finally:
            if "db" in locals():
                db.close()

    async def get_user_by_session(self, session_id: str) -> Optional[User]:
        """
        Get user by session ID.

        Args:
            session_id: Session identifier

        Returns:
            User instance or None if session invalid
        """
        try:
            db: Session = next(get_db())

            session = (
                db.query(UserSession)
                .filter(
                    UserSession.session_id == session_id,
                    UserSession.expires_at > datetime.utcnow(),
                )
                .first()
            )

            if not session:
                return None

            user = db.query(User).filter(User.id == session.user_id).first()
            return user

        except Exception as e:
            logger.error(f"Error getting user by session: {e}")
            return None
        finally:
            if "db" in locals():
                db.close()

    async def invalidate_session(self, session_id: str) -> bool:
        """
        Invalidate a user session by deleting it from the database.

        Args:
            session_id: Session identifier to invalidate

        Returns:
            True if session was invalidated, False otherwise
        """
        try:
            db: Session = next(get_db())

            result = (
                db.query(UserSession)
                .filter(UserSession.session_id == session_id)
                .delete()
            )
            db.commit()

            if result > 0:
                logger.info(f"Invalidated session {session_id[:8]}...")
                return True
            return False

        except Exception as e:
            logger.error(f"Error invalidating session: {e}")
            if "db" in locals():
                db.rollback()
            return False
        finally:
            if "db" in locals():
                db.close()

    async def refresh_token(self, user: User) -> Optional[str]:
        """
        Refresh an expired access token using the refresh token.

        Args:
            user: User instance

        Returns:
            New access token or None if refresh failed
        """
        try:
            db: Session = next(get_db())

            token = (
                db.query(OAuthToken)
                .filter(OAuthToken.user_id == user.id)
                .order_by(OAuthToken.created_at.desc())
                .first()
            )

            if not token or not token.refresh_token:
                logger.warning(f"No refresh token available for user {user.username}")
                return None

            refresh_data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
            }

            headers = {
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            }

            response = requests.post(
                f"{self.github_oauth_base}/access_token",
                data=refresh_data,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()

            token_response = response.json()

            if "error" in token_response:
                logger.error(f"Token refresh error: {token_response}")
                return None

            new_access_token = token_response.get("access_token")
            if not new_access_token:
                return None

            expires_in = token_response.get("expires_in")
            expires_at = None
            if expires_in:
                expires_at = datetime.utcnow() + timedelta(seconds=int(expires_in))

            token.access_token = new_access_token
            if token_response.get("refresh_token"):
                token.refresh_token = token_response["refresh_token"]
            token.expires_at = expires_at
            token.updated_at = datetime.utcnow()

            db.commit()
            logger.info(f"Refreshed token for user {user.username}")
            return new_access_token

        except requests.RequestException as e:
            logger.error(f"Error refreshing token: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error refreshing token: {e}")
            if "db" in locals():
                db.rollback()
            return None
        finally:
            if "db" in locals():
                db.close()

    async def create_webhook(
        self, access_token: str, owner: str, repo: str
    ) -> Optional[Dict[str, Any]]:
        """
        Create a webhook on a GitHub repository.

        Args:
            access_token: User's GitHub access token
            owner: Repository owner
            repo: Repository name

        Returns:
            Dictionary with webhook_id and webhook_secret, or None if failed
        """
        try:
            webhook_secret = secrets.token_urlsafe(32)
            webhook_url = f"{APP_URL}/api/prs/github-webhook-oauth"

            webhook_data = {
                "name": "web",
                "active": True,
                "events": [
                    "pull_request",
                    "push",
                    "issue_comment",
                    "pull_request_review",
                ],
                "config": {
                    "url": webhook_url,
                    "content_type": "json",
                    "secret": webhook_secret,
                    "insecure_ssl": "0",
                },
            }

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            }

            response = requests.post(
                f"{self.github_api_base}/repos/{owner}/{repo}/hooks",
                json=webhook_data,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()

            webhook_response = response.json()
            webhook_id = webhook_response.get("id")

            logger.info(f"Created webhook {webhook_id} for {owner}/{repo}")
            return {"webhook_id": webhook_id, "webhook_secret": webhook_secret}

        except requests.RequestException as e:
            logger.error(f"Error creating webhook for {owner}/{repo}: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error creating webhook: {e}")
            return None

    async def delete_webhook(
        self, access_token: str, owner: str, repo: str, webhook_id: int
    ) -> bool:
        """
        Delete a webhook from a GitHub repository.

        Args:
            access_token: User's GitHub access token
            owner: Repository owner
            repo: Repository name
            webhook_id: ID of the webhook to delete

        Returns:
            True if webhook was deleted, False otherwise
        """
        try:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }

            response = requests.delete(
                f"{self.github_api_base}/repos/{owner}/{repo}/hooks/{webhook_id}",
                headers=headers,
                timeout=30,
            )

            if response.status_code == 204:
                logger.info(f"Deleted webhook {webhook_id} from {owner}/{repo}")
                return True
            elif response.status_code == 404:
                logger.warning(f"Webhook {webhook_id} not found on {owner}/{repo}")
                return True

            response.raise_for_status()
            return True

        except requests.RequestException as e:
            logger.error(
                f"Error deleting webhook {webhook_id} from {owner}/{repo}: {e}"
            )
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error deleting webhook: {e}")
            return False
