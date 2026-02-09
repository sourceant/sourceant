"""
Database models for GitHub OAuth plugin.
"""

from typing import Optional, List
from datetime import datetime
from sqlmodel import Field, Relationship
from src.models.base_model import BaseModel


class User(BaseModel, table=True):
    """User model for authenticated GitHub users."""

    __tablename__ = "users"

    github_id: int = Field(unique=True, nullable=False, index=True)
    username: str = Field(max_length=255, nullable=False, index=True)
    email: Optional[str] = Field(default=None, max_length=255)
    name: Optional[str] = Field(default=None, max_length=255)
    avatar_url: Optional[str] = Field(default=None)

    oauth_tokens: List["OAuthToken"] = Relationship(back_populates="user")
    repositories: List["UserRepository"] = Relationship(back_populates="user")

    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}', github_id={self.github_id})>"


class OAuthToken(BaseModel, table=True):
    """OAuth token storage for GitHub authentication."""

    __tablename__ = "oauth_tokens"

    user_id: int = Field(foreign_key="users.id", nullable=False, index=True)
    access_token: str = Field(nullable=False)
    refresh_token: Optional[str] = Field(default=None)
    token_type: str = Field(default="bearer", max_length=50)
    scope: Optional[str] = Field(default=None)
    expires_at: Optional[datetime] = Field(default=None)

    user: Optional["User"] = Relationship(back_populates="oauth_tokens")

    def __repr__(self):
        return f"<OAuthToken(id={self.id}, user_id={self.user_id}, token_type='{self.token_type}')>"


class UserRepository(BaseModel, table=True):
    """Association between users and their authorized repositories."""

    __tablename__ = "user_repositories"

    user_id: int = Field(foreign_key="users.id", nullable=False, index=True)
    github_repo_id: int = Field(nullable=False, index=True)
    full_name: str = Field(max_length=255, nullable=False)
    owner: str = Field(max_length=255, nullable=False)
    name: str = Field(max_length=255, nullable=False)
    private: bool = Field(default=False, nullable=False)
    webhook_configured: bool = Field(default=False, nullable=False)
    webhook_id: Optional[int] = Field(default=None)
    webhook_secret: Optional[str] = Field(default=None, max_length=255)
    enabled: bool = Field(default=True, nullable=False)

    user: Optional["User"] = Relationship(back_populates="repositories")

    def __repr__(self):
        return f"<UserRepository(id={self.id}, user_id={self.user_id}, full_name='{self.full_name}')>"


class UserSession(BaseModel, table=True):
    """User session management."""

    __tablename__ = "user_sessions"

    session_id: str = Field(max_length=255, unique=True, nullable=False, index=True)
    user_id: int = Field(foreign_key="users.id", nullable=False, index=True)
    csrf_token: Optional[str] = Field(default=None, max_length=255)
    expires_at: datetime = Field(nullable=False)

    user: Optional["User"] = Relationship()

    def __repr__(self):
        return f"<UserSession(id={self.id}, user_id={self.user_id}, session_id='{self.session_id[:8]}...')>"
