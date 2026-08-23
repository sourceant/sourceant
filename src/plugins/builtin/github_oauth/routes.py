"""
FastAPI routes for GitHub OAuth plugin.
"""

import secrets
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Response, HTTPException, Depends, Cookie
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.status import HTTP_302_FOUND, HTTP_400_BAD_REQUEST, HTTP_401_UNAUTHORIZED

from .oauth_handler import GitHubOAuthHandler
from .models import User, UserRepository
from src.config.db import get_db
from src.utils.logger import logger

router = APIRouter(prefix="/auth/github", tags=["github-oauth"])


class GitHubOAuthRoutes:
    """GitHub OAuth routes handler."""

    def __init__(self, oauth_handler: GitHubOAuthHandler):
        """Initialize routes with OAuth handler."""
        self.oauth_handler = oauth_handler
        self._setup_routes()

    def _setup_routes(self):
        """Setup route handlers."""

        @router.get("/login")
        async def initiate_oauth(request: Request, response: Response):
            """
            Initiate GitHub OAuth flow.

            Generates authorization URL and redirects user to GitHub.
            """
            try:
                # Generate auth URL with state and PKCE
                auth_data = self.oauth_handler.generate_auth_url()

                # Store state and code_verifier in session/cookies for validation
                response.set_cookie(
                    key="oauth_state",
                    value=auth_data["state"],
                    max_age=600,  # 10 minutes
                    httponly=True,
                    secure=True,
                    samesite="lax",
                )
                response.set_cookie(
                    key="oauth_code_verifier",
                    value=auth_data["code_verifier"],
                    max_age=600,  # 10 minutes
                    httponly=True,
                    secure=True,
                    samesite="lax",
                )

                logger.info("Initiating GitHub OAuth flow")
                return RedirectResponse(
                    url=auth_data["auth_url"], status_code=HTTP_302_FOUND
                )

            except Exception as e:
                logger.error(f"Error initiating OAuth: {e}")
                raise HTTPException(status_code=500, detail="Failed to initiate OAuth")

        @router.get("/callback")
        async def oauth_callback(
            request: Request,
            response: Response,
            code: Optional[str] = None,
            state: Optional[str] = None,
            error: Optional[str] = None,
            oauth_state: Optional[str] = Cookie(None),
            oauth_code_verifier: Optional[str] = Cookie(None),
        ):
            """
            Handle OAuth callback from GitHub.

            Exchanges authorization code for access token and creates user session.
            """
            try:
                # Clear OAuth cookies
                response.delete_cookie("oauth_state")
                response.delete_cookie("oauth_code_verifier")

                # Handle OAuth errors
                if error:
                    logger.warning(f"OAuth error: {error}")
                    return JSONResponse(
                        content={
                            "error": "OAuth authorization failed",
                            "details": error,
                        },
                        status_code=HTTP_400_BAD_REQUEST,
                    )

                # Validate required parameters
                if not code or not state or not oauth_state or not oauth_code_verifier:
                    logger.warning("Missing required OAuth parameters")
                    raise HTTPException(
                        status_code=HTTP_400_BAD_REQUEST,
                        detail="Missing required OAuth parameters",
                    )

                # Validate state parameter (CSRF protection)
                if state != oauth_state:
                    logger.warning(
                        f"Invalid OAuth state: expected {oauth_state}, got {state}"
                    )
                    raise HTTPException(
                        status_code=HTTP_400_BAD_REQUEST,
                        detail="Invalid state parameter",
                    )

                # Exchange code for token
                token_info = await self.oauth_handler.exchange_code_for_token(
                    code=code, code_verifier=oauth_code_verifier, state=state
                )

                if not token_info or "access_token" not in token_info:
                    logger.error("Failed to exchange code for token")
                    raise HTTPException(
                        status_code=HTTP_400_BAD_REQUEST,
                        detail="Failed to obtain access token",
                    )

                # Get user information
                user_info = await self.oauth_handler.get_user_info(
                    token_info["access_token"]
                )
                if not user_info:
                    logger.error("Failed to get user information")
                    raise HTTPException(
                        status_code=HTTP_400_BAD_REQUEST,
                        detail="Failed to get user information",
                    )

                # Create or update user
                user = await self.oauth_handler.create_or_update_user(
                    user_info, token_info
                )
                if not user:
                    logger.error("Failed to create/update user")
                    raise HTTPException(
                        status_code=HTTP_500, detail="Failed to create user account"
                    )

                # Get user repositories
                repositories = await self.oauth_handler.get_user_repositories(
                    token_info["access_token"]
                )
                if repositories:
                    await self.oauth_handler.update_user_repositories(
                        user, repositories
                    )

                # Create user session
                session_id = await self.oauth_handler.create_user_session(user)
                if not session_id:
                    logger.error("Failed to create user session")
                    raise HTTPException(
                        status_code=HTTP_500, detail="Failed to create user session"
                    )

                # Set session cookie
                response.set_cookie(
                    key="session_id",
                    value=session_id,
                    max_age=24 * 60 * 60,  # 24 hours
                    httponly=True,
                    secure=True,
                    samesite="lax",
                )

                logger.info(f"Successfully authenticated user: {user.username}")

                return JSONResponse(
                    content={
                        "message": "Authentication successful",
                        "user": {
                            "id": user.id,
                            "username": user.username,
                            "name": user.name,
                            "avatar_url": user.avatar_url,
                        },
                    }
                )

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error in OAuth callback: {e}")
                raise HTTPException(status_code=500, detail="Internal server error")

        @router.post("/logout")
        async def logout(response: Response, session_id: Optional[str] = Cookie(None)):
            """Log out user and clear session."""
            if session_id:
                await self.oauth_handler.invalidate_session(session_id)
                response.delete_cookie("session_id")
                logger.info("User logged out")

            return JSONResponse(content={"message": "Logged out successfully"})

        @router.get("/user")
        async def get_current_user(session_id: Optional[str] = Cookie(None)):
            """Get current authenticated user information."""
            if not session_id:
                raise HTTPException(
                    status_code=HTTP_401_UNAUTHORIZED, detail="Not authenticated"
                )

            user = await self.oauth_handler.get_user_by_session(session_id)
            if not user:
                raise HTTPException(
                    status_code=HTTP_401_UNAUTHORIZED, detail="Invalid session"
                )

            return {
                "id": user.id,
                "github_id": user.github_id,
                "username": user.username,
                "name": user.name,
                "email": user.email,
                "avatar_url": user.avatar_url,
                "created_at": user.created_at.isoformat(),
                "updated_at": user.updated_at.isoformat(),
            }

        @router.get("/repositories")
        async def get_user_repositories(session_id: Optional[str] = Cookie(None)):
            """Get user's authorized repositories."""
            if not session_id:
                raise HTTPException(
                    status_code=HTTP_401_UNAUTHORIZED, detail="Not authenticated"
                )

            user = await self.oauth_handler.get_user_by_session(session_id)
            if not user:
                raise HTTPException(
                    status_code=HTTP_401_UNAUTHORIZED, detail="Invalid session"
                )

            try:
                db = next(get_db())
                repositories = (
                    db.query(UserRepository)
                    .filter(
                        UserRepository.user_id == user.id,
                        UserRepository.enabled == True,
                    )
                    .all()
                )

                return [
                    {
                        "id": repo.id,
                        "github_repo_id": repo.github_repo_id,
                        "full_name": repo.full_name,
                        "owner": repo.owner,
                        "name": repo.name,
                        "private": repo.private,
                        "webhook_configured": repo.webhook_configured,
                        "enabled": repo.enabled,
                        "created_at": repo.created_at.isoformat(),
                        "updated_at": repo.updated_at.isoformat(),
                    }
                    for repo in repositories
                ]

            except Exception as e:
                logger.error(f"Error getting user repositories: {e}")
                raise HTTPException(
                    status_code=500, detail="Failed to get repositories"
                )
            finally:
                db.close()

        @router.post("/repositories/{repo_id}/webhook")
        async def configure_webhook(
            repo_id: int, session_id: Optional[str] = Cookie(None)
        ):
            """Configure webhook for a repository."""
            if not session_id:
                raise HTTPException(
                    status_code=HTTP_401_UNAUTHORIZED, detail="Not authenticated"
                )

            user = await self.oauth_handler.get_user_by_session(session_id)
            if not user:
                raise HTTPException(
                    status_code=HTTP_401_UNAUTHORIZED, detail="Invalid session"
                )

            try:
                db = next(get_db())

                # Get repository
                repository = (
                    db.query(UserRepository)
                    .filter(
                        UserRepository.id == repo_id, UserRepository.user_id == user.id
                    )
                    .first()
                )

                if not repository:
                    raise HTTPException(status_code=404, detail="Repository not found")

                # Get user's access token
                access_token = await self.oauth_handler.get_valid_token(user)
                if not access_token:
                    raise HTTPException(
                        status_code=HTTP_401_UNAUTHORIZED,
                        detail="No valid access token",
                    )

                webhook_result = await self.oauth_handler.create_webhook(
                    access_token=access_token,
                    owner=repository.owner,
                    repo=repository.name,
                )

                if not webhook_result:
                    raise HTTPException(
                        status_code=500, detail="Failed to create webhook on GitHub"
                    )

                repository.webhook_id = webhook_result["webhook_id"]
                repository.webhook_secret = webhook_result["webhook_secret"]
                repository.webhook_configured = True
                repository.updated_at = datetime.utcnow()
                db.commit()

                logger.info(f"Configured webhook for repository {repository.full_name}")

                return {"message": "Webhook configured successfully"}

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error configuring webhook: {e}")
                if "db" in locals():
                    db.rollback()
                raise HTTPException(
                    status_code=500, detail="Failed to configure webhook"
                )
            finally:
                if "db" in locals():
                    db.close()

        @router.delete("/repositories/{repo_id}/webhook")
        async def remove_webhook(
            repo_id: int, session_id: Optional[str] = Cookie(None)
        ):
            """Remove webhook for a repository."""
            if not session_id:
                raise HTTPException(
                    status_code=HTTP_401_UNAUTHORIZED, detail="Not authenticated"
                )

            user = await self.oauth_handler.get_user_by_session(session_id)
            if not user:
                raise HTTPException(
                    status_code=HTTP_401_UNAUTHORIZED, detail="Invalid session"
                )

            try:
                db = next(get_db())

                # Get repository
                repository = (
                    db.query(UserRepository)
                    .filter(
                        UserRepository.id == repo_id, UserRepository.user_id == user.id
                    )
                    .first()
                )

                if not repository:
                    raise HTTPException(status_code=404, detail="Repository not found")

                if repository.webhook_id:
                    access_token = await self.oauth_handler.get_valid_token(user)
                    if access_token:
                        await self.oauth_handler.delete_webhook(
                            access_token=access_token,
                            owner=repository.owner,
                            repo=repository.name,
                            webhook_id=repository.webhook_id,
                        )

                repository.webhook_configured = False
                repository.webhook_id = None
                repository.webhook_secret = None
                repository.updated_at = datetime.utcnow()
                db.commit()

                logger.info(f"Removed webhook for repository {repository.full_name}")

                return {"message": "Webhook removed successfully"}

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error removing webhook: {e}")
                if "db" in locals():
                    db.rollback()
                raise HTTPException(status_code=500, detail="Failed to remove webhook")
            finally:
                if "db" in locals():
                    db.close()
