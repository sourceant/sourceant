"""
Database models for GitHub OAuth plugin.
"""

from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from src.models.base_model import BaseModel


class User(BaseModel):
    """User model for authenticated GitHub users."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    github_id = Column(Integer, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=False, index=True)
    email = Column(String(255), nullable=True)
    name = Column(String(255), nullable=True)
    avatar_url = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    oauth_tokens = relationship(
        "OAuthToken", back_populates="user", cascade="all, delete-orphan"
    )
    repositories = relationship(
        "UserRepository", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}', github_id={self.github_id})>"


class OAuthToken(BaseModel):
    """OAuth token storage for GitHub authentication."""

    __tablename__ = "oauth_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    token_type = Column(String(50), nullable=False, default="bearer")
    scope = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user = relationship("User", back_populates="oauth_tokens")

    def __repr__(self):
        return f"<OAuthToken(id={self.id}, user_id={self.user_id}, token_type='{self.token_type}')>"


class UserRepository(BaseModel):
    """Association between users and their authorized repositories."""

    __tablename__ = "user_repositories"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    github_repo_id = Column(Integer, nullable=False, index=True)
    full_name = Column(String(255), nullable=False)  # owner/repo format
    owner = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    private = Column(Boolean, nullable=False, default=False)
    webhook_configured = Column(Boolean, nullable=False, default=False)
    webhook_id = Column(Integer, nullable=True)
    webhook_secret = Column(String(255), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user = relationship("User", back_populates="repositories")

    def __repr__(self):
        return f"<UserRepository(id={self.id}, user_id={self.user_id}, full_name='{self.full_name}')>"


class UserSession(BaseModel):
    """User session management."""

    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(255), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    csrf_token = Column(String(255), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user = relationship("User")

    def __repr__(self):
        return f"<UserSession(id={self.id}, user_id={self.user_id}, session_id='{self.session_id[:8]}...')>"
