from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class OrganizationRoutingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,38}/\*$")
    workspace_id: int = Field(gt=0, strict=True)


class RepositoryRoutingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_workspace_id: int | None = Field(default=None, gt=0, strict=True)
    accept_unconnected: StrictBool = False
    match_organization: StrictBool = False
    organization_rules: list[OrganizationRoutingRule] = Field(
        default_factory=list, max_length=100
    )

    @model_validator(mode="after")
    def distinct_organizations(self):
        patterns = [rule.pattern.lower() for rule in self.organization_rules]
        if len(set(patterns)) != len(patterns):
            raise ValueError("Organization rules must be distinct")
        for rule in self.organization_rules:
            rule.pattern = rule.pattern.lower()
        return self
