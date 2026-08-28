from sqlmodel import Field

from src.models.base_model import BaseModel


class Workspace(BaseModel, table=True):
    """A workspace this deployment has been asked to act in.

    The gateway owns what a workspace is: its name, who belongs to it, what they
    may do. Recorded here so that what belongs to one can point at it, and so
    that nothing belongs to a workspace that was never seen.

    Nothing about the workspace itself is copied. A name kept here would be a
    second answer to a question the gateway already answers, and the two would
    disagree the first time one was renamed.
    """

    __tablename__ = "workspaces"

    #: What the gateway calls this workspace, and what its tokens name. Unique,
    #: because it is the identity everything outside this deployment uses.
    external_ref: str = Field(unique=True, index=True, max_length=255)
