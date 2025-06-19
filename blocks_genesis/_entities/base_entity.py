from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

class BaseEntity(BaseModel):
    _id: str = Field(..., alias="id")
    CreatedDate: datetime = Field(default_factory=datetime.now)
    LastUpdatedDate: datetime = Field(default_factory=datetime.now)
    CreatedBy: Optional[str] = None
    Language: Optional[str] = None
    LastUpdatedBy: Optional[str] = None
    OrganizationIds: List[str] = Field(default_factory=list)
    Tags: List[str] = Field(default_factory=list)

    class Config:
        allow_population_by_field_name = True  # allows use of "_id" or "id" when constructing
