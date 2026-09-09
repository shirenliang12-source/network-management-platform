"""Serial number decoder API routes."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.services.serial_decoder import decode_serial, batch_decode
from app.schemas import SerialDecodeResult

router = APIRouter(prefix="/api/serial", tags=["serial"])


@router.get("/decode/{serial_number}")
def decode(serial_number: str):
    """Decode a single Cisco serial number."""
    return decode_serial(serial_number)


@router.post("/decode-batch")
def decode_batch(serials: List[str]):
    """Decode multiple Cisco serial numbers."""
    return batch_decode(serials)
