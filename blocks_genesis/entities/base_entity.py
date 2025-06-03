from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field
from bson import ObjectId

class BaseEntity(BaseModel):
    id: str = Field(alias="_id")  # MongoDB _id field
    created_date: datetime = Field(alias="CreatedDate", default_factory=datetime.now)
    last_updated_date: datetime = Field(alias="LastUpdatedDate", default_factory=datetime.now)
    created_by: Optional[str] = Field(alias="CreatedBy", default=None)
    language: Optional[str] = Field(alias="Language", default=None)
    last_updated_by: Optional[str] = Field(alias="LastUpdatedBy", default=None)
    organization_ids: List[str] = Field(alias="OrganizationIds", default_factory=list)
    tags: List[str] = Field(alias="Tags", default_factory=list)
    
    class Config:
        # Ignore extra fields (equivalent to BsonIgnoreExtraElements)
        extra = "ignore"
        # Allow population by field name or alias
        allow_population_by_field_name = True