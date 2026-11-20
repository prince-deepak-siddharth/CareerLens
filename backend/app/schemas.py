from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
    model_validator,
)

Text120 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
SkillList = Annotated[list[Text120], Field(max_length=80)]
Employment = Literal["internship", "full-time", "part-time", "contract"]
Remote = Literal["remote", "hybrid", "onsite"]
Mode = Literal["keyword", "semantic", "hybrid", "reranked", "personalized"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProfileData(Strict):
    education: SkillList = Field(default_factory=list)
    skills: SkillList = Field(default_factory=list)
    projects: SkillList = Field(default_factory=list)
    work_experience: SkillList = Field(default_factory=list)
    tools: SkillList = Field(default_factory=list)
    certifications: SkillList = Field(default_factory=list)
    domains: SkillList = Field(default_factory=list)
    experience_level: Literal["student", "entry", "mid", "senior"] | None = None
    years_experience: int | None = Field(None, ge=0, le=70)


class Filters(Strict):
    source: Literal["live", "demo"] | None = None
    employment_type: Employment | None = None
    remote_policy: Remote | None = None
    location: Text120 | None = None
    max_required_years: int | None = Field(None, ge=0, le=70)
    min_salary: int | None = Field(None, ge=1, le=10_000_000)
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    skills: Annotated[list[Text120], Field(max_length=12)] = Field(default_factory=list)
    company_category: Text120 | None = None
    posted_within_days: int | None = Field(None, ge=1, le=365)

    @field_validator("skills")
    @classmethod
    def normalize_skills(cls, values):
        return sorted({item.casefold() for item in values})

    @model_validator(mode="after")
    def salary_currency(self):
        if self.min_salary is not None and not self.currency:
            raise ValueError("Currency is required with a salary filter")
        return self


class Search(Strict):
    query: str = Field("", max_length=600)
    filters: Filters = Field(default_factory=Filters)
    mode: Mode = "personalized"
    limit: int = Field(12, ge=1, le=50)


class JobImport(Strict):
    external_id: Text120
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    company: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    description: str = Field(min_length=1, max_length=24000)
    required_skills: SkillList
    preferred_skills: SkillList = Field(default_factory=list)
    min_years: int | None = Field(None, ge=0, le=70)
    employment_type: Employment | None = None
    location: Annotated[str, StringConstraints(min_length=1, max_length=160)] | None = None
    remote_policy: Remote | None = None
    company_category: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    salary_min: int | None = Field(None, ge=0)
    salary_max: int | None = Field(None, ge=0)
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    posted_at: datetime | None = None
    updated_at: datetime
    expires_at: datetime | None = None
    source_url: HttpUrl | None = None
    active: bool = True

    @field_validator("required_skills", "preferred_skills")
    @classmethod
    def normalize_skills(cls, values):
        return sorted({item.casefold() for item in values})

    @model_validator(mode="after")
    def check_metadata(self):
        for value in (self.posted_at, self.updated_at, self.expires_at):
            if value is not None and value.utcoffset() is None:
                raise ValueError("Job dates must include timezone information")
        if (self.salary_min is not None or self.salary_max is not None) and not self.currency:
            raise ValueError("Compensation requires currency")
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError("Invalid compensation range")
        return self


class JobView(JobImport):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    synthetic: bool
    source_name: str
    source_provenance: str


class Match(Strict):
    job: JobView
    score: float | None = None
    signals: dict[str, float] = Field(default_factory=dict)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    eligibility_warnings: list[str] = Field(default_factory=list)
    evidence: str
    explanation: str


class SearchResults(Strict):
    items: list[Match]
    mode: Mode
    filters: Filters
    ranking_version: str
    reranked: bool = False
    embedding_model: str | None = None
