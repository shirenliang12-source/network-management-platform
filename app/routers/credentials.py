"""Credential profile management API routes."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models import CredentialProfile
from app.services.nic_service import get_preferred_source_ip
from app.schemas import (
    CredentialProfileCreate,
    CredentialProfileUpdate,
    CredentialProfileResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/credentials", tags=["credentials"])


@router.get("/profiles", response_model=List[CredentialProfileResponse])
def list_profiles(db: Session = Depends(get_db)):
    """List all credential profiles."""
    profiles = db.query(CredentialProfile).order_by(CredentialProfile.name).all()
    return [_to_response(p) for p in profiles]


@router.get("/profiles/{profile_id}", response_model=CredentialProfileResponse)
def get_profile(profile_id: int, db: Session = Depends(get_db)):
    """Get a single credential profile."""
    p = db.query(CredentialProfile).get(profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Credential profile not found")
    return _to_response(p)


@router.post("/profiles", response_model=CredentialProfileResponse)
def create_profile(profile: CredentialProfileCreate, db: Session = Depends(get_db)):
    """Create a new credential profile."""
    p = CredentialProfile(
        name=profile.name,
        device_type=profile.device_type,
        username=profile.username,
        port=profile.port,
        source_ip=profile.source_ip or get_preferred_source_ip(db),
        description=profile.description or "",
    )
    p.set_password(profile.password)
    p.set_enable_password(profile.enable_password)
    db.add(p)
    db.commit()
    db.refresh(p)
    return _to_response(p)


@router.put("/profiles/{profile_id}", response_model=CredentialProfileResponse)
def update_profile(profile_id: int, profile: CredentialProfileUpdate, db: Session = Depends(get_db)):
    """Update an existing credential profile."""
    p = db.query(CredentialProfile).get(profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Credential profile not found")

    update_data = profile.model_dump(exclude_unset=True)
    password = update_data.pop("password", None)
    enable_password = update_data.pop("enable_password", None)

    for key, value in update_data.items():
        setattr(p, key, value)

    if password is not None:
        p.set_password(password)
    if enable_password is not None:
        p.set_enable_password(enable_password)

    db.commit()
    db.refresh(p)
    return _to_response(p)


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    """Delete a credential profile."""
    p = db.query(CredentialProfile).get(profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Credential profile not found")
    db.delete(p)
    db.commit()
    return {"message": "Credential profile deleted"}


def _to_response(p: CredentialProfile) -> CredentialProfileResponse:
    return CredentialProfileResponse(
        id=p.id,
        name=p.name,
        device_type=p.device_type,
        username=p.username,
        port=p.port,
        source_ip=p.source_ip or "",
        description=p.description or "",
        has_password=bool(p.password_enc),
        has_enable_password=bool(p.enable_password_enc),
        created_at=p.created_at,
        updated_at=p.updated_at,
    )
