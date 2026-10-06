from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, field_validator


class ModelEndpoint(BaseModel):
    base_url: str = ""

    @field_validator("base_url")
    @classmethod
    def validate_endpoint(cls, value):
        if value:
            from .catalogue import reachable

            TypeAdapter(HttpUrl).validate_python(value)
            reachable(value)
        return value


class ModelProfile(ModelEndpoint):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    base_url: str = ""
    token_limit: int = Field(default=8192, ge=1)
    cache_prompts: bool = True


class ProfileCredential(ModelEndpoint):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1)
    base_url: str = ""
    api_key: str = ""
