"""
Cisco serial number decoder + raw-output manufacturing-date extractor.

Cisco serial numbers follow several formats depending on the contract manufacturer
and product line. This module attempts to decode the manufacturing location and
estimate the production date from the serial number encoding.

Known formats:
  - Old format (pre-2010):   e.g., "FAB12345678"  (3-letter location + 8 digits)
  - New format (2010+):       e.g., "FOC1234X5YZ"  (3-letter location + 4 digits + 4 alphanumeric)
  - 11-char standard:         e.g., "FXS1234ABCD"

The first 3 characters typically represent the manufacturing location / facility code.
Date estimation is based on known patterns from community research; it is NOT 100% accurate.

For actual manufacturing dates, also try `extract_production_date_from_outputs()`,
which parses the raw output of `show inventory` / `show diag` / etc. — those often
contain explicit "Mfg Data: YYWW" or "Manufacture Date: YYYY-MM-DD" lines that are
far more reliable than anything encoded in the serial number.
"""
import re
from datetime import date


# Known Cisco manufacturing location codes (3-letter prefix)
LOCATION_CODES = {
    "FAB": "Brazil (Manaus)",
    "FAL": "USA (San Jose)",
    "FOC": "Mexico (Guadalajara - Foxconn)",
    "FOX": "Mexico (Foxconn)",
    "FXS": "China (Shenzhen - Foxconn)",
    "FZC": "China (Zhuhai - Flex)",
    "FZH": "China (Zhangjiagang)",
    "GAB": "Malaysia (Penang)",
    "GAK": "Korea",
    "GCS": "China (Shenzhen - Celestica)",
    "JAB": "Japan",
    "JMX": "Mexico (Jabil)",
    "JPE": "China (Jabil - Peking)",
    "KAZ": "Malaysia (Kuala Lumpur)",
    "PEN": "Malaysia (Penang)",
    "PIN": "USA",
    "QFI": "China (Flex - PNE)",
    "QPE": "China (Peking)",
    "QZS": "China (Shanghai - Jabil)",
    "RDC": "China (Dongguan)",
    "SEP": "Singapore",
    "SGA": "Singapore",
    "SZM": "China (Shenzhen)",
    "TED": "China",
    "TMO": "Mexico (Tijuana)",
    "TXN": "USA (Texas)",
    "VCO": "Mexico",
    "WCH": "China (Wuhan)",
    "WTM": "Taiwan",
    "ZBA": "China",
    "ZCD": "China (Dongguan)",
    "ZHA": "China",
    "ZST": "China (Shenzhen)",
    "ZSZ": "China (Shenzhen)",
    "ZWH": "China (Wuhan)",
}


def decode_serial(serial_number: str) -> dict:
    """
    Decode a Cisco serial number to extract location and estimated production date.

    Returns a dict with:
        - format: serial format description
        - location_code: 3-letter prefix
        - location_name: human-readable location
        - production_date: estimated date string (YYYY-MM or YYYY)
        - is_estimated: always True (no official API used)
        - notes: additional information
    """
    serial = serial_number.strip().upper()
    result = {
        "serial_number": serial,
        "format": "",
        "location_code": "",
        "location_name": "",
        "production_date": "",
        "is_estimated": True,
        "notes": "",
    }

    if not serial:
        result["notes"] = "Empty serial number."
        return result

    # Extract the 3-letter location code
    location_code = serial[:3]
    result["location_code"] = location_code
    result["location_name"] = LOCATION_CODES.get(location_code, f"Unknown ({location_code})")

    # Determine format
    if len(serial) == 11:
        if serial[3:7].isdigit():
            result["format"] = "Standard 11-char (3-letter location + 4 digits + 4 alphanumeric)"
        else:
            result["format"] = "11-char variant"
    elif len(serial) == 10:
        result["format"] = "10-char format"
    elif len(serial) == 12:
        result["format"] = "12-char format"
    else:
        result["format"] = f"Non-standard ({len(serial)} chars)"

    # Attempt date estimation
    estimated_date = _estimate_production_date(serial)
    result["production_date"] = estimated_date["date"]
    result["notes"] = estimated_date["notes"]

    return result


def _estimate_production_date(serial: str) -> dict:
    """
    Attempt to estimate production date from serial number.

    NOTE: Cisco serial numbers do NOT contain a publicly documented, reliable
    production-date encoding. Community "sequence number" heuristics correlate a
    device's sequential number with a rough manufacturing era, but they are wildly
    inaccurate (e.g. a serial containing the digits "2019" would be mapped to 2008).
    Showing such a value as a factual "生产日期" is misleading, so we no longer
    fabricate a year here. The manufacturing location (3-letter prefix) is reliable
    and is still reported.

    If a real manufacturing date is needed, prefer `extract_production_date_from_outputs()`
    which scans raw CLI output for explicit "Mfg Data: YYWW" or similar lines, or
    enter the date manually in the asset record.
    """
    return {
        "date": "",
        "notes": (
            "Cisco 序列号不含可靠的出厂日期编码；"
            "请使用「采集命令配置」额外抓取 show inventory/show diag 等命令，"
            "或手动填写出厂日期。"
        ),
    }


def batch_decode(serials: list) -> list:
    """Decode a batch of serial numbers."""
    return [decode_serial(s) for s in serials]


# =====================================================================
# Production-date extraction from raw CLI outputs (v1.9.35+)
# =====================================================================
#
# Many Cisco platforms disclose manufacturing / production dates through
# the raw text of certain show commands. Common patterns across IOS /
# IOS-XE / NX-OS / WLC include:
#
#   PID: WS-X6748-GE-TX  , VID: V05  , SN: SAL1330XXXXX
#   Mfg Data: 2013 30               <- YY + WW of ISO week-of-year
#
#   Manufacture Date: 2018-04-15
#   Mfg Date: 2019/03/12
#   Manufactured: 2020-08
#   Production Date: 2017-11-22
#
# We try each (label, regex) pair in order; the first match wins. YYWW
# is converted to the Monday of that ISO week to produce a real date.

# Order matters: more specific patterns first.
_PRODUCTION_DATE_PATTERNS: list[tuple[str, str]] = [
    # Explicit ISO-ish date "Manufacture Date: 2018-04-15"
    ("Manufacture Date",
     r"Manufacture\s*Date\s*[:\-]\s*(\d{4}[\-\./]\d{1,2}[\-\./]\d{1,2})"),
    # "Manufacturing Date: 2018-04-15"
    ("Manufacturing Date",
     r"Manufacturing\s*Date\s*[:\-]\s*(\d{4}[\-\./]\d{1,2}[\-\./]\d{1,2})"),
    # "Mfg Date: 2020-05-12" or "Mfg. Date: 2020/05/12"
    ("Mfg Date",
     r"Mfg\.?\s+Date\s*[:\-]\s*(\d{4}[\-\./]\d{1,2}[\-\./]\d{1,2})"),
    # "Manufactured: 2018-04"
    ("Manufactured",
     r"Manufactured\s*[:\-]\s*(\d{4}[\-\./]\d{1,2}[\-\./]?\d{0,2})"),
    # "Production Date: 2017-11-22"
    ("Production Date",
     r"Production\s*Date\s*[:\-]\s*(\d{4}[\-\./]\d{1,2}[\-\./]\d{1,2})"),
    # "Date of manufacture: ..."
    ("Date of manufacture",
     r"Date\s+of\s+[Mm]anufacture\s*[:\-]\s*(\d{4}[\-\./]\d{1,2}[\-\./]?\d{0,2})"),
    # "Built on 2018-04-15"
    ("Built on",
     r"Built\s+(?:on|at)\s+(\d{4}[\-\./]\d{1,2}[\-\./]\d{1,2})"),
    # YY + ISO-week (Cisco linecard Mfg Data) — both 2-digit and 4-digit years
    ("Mfg Data (YYWW)",
     r"Mfg\.?\s+Data\s*[:\-]\s*(\d{2}|\d{4})[ \t]+(\d{1,2})\b"),
]


def _normalize_date_text(date_text: str) -> str:
    """Normalize matched date text to ISO format YYYY-MM-DD.

    Accepts inputs like "2018-04-15", "2018/04/15", "2018.04.15", "2018-04".
    Returns the original string when it does not look like a date.
    """
    s = (date_text or "").strip()
    if not s:
        return ""
    # All non-digit separators become "-"
    normalized = re.sub(r"[^\d]", "-", s)
    # Drop trailing empty fields
    parts = [p for p in normalized.split("-") if p]
    if len(parts) >= 3 and len(parts[0]) == 4:
        year, month, day = parts[0], parts[1].zfill(2), parts[2].zfill(2)
        try:
            y, m, d = int(year), int(month), int(day)
            if 1990 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{m:02d}-{d:02d}"
        except ValueError:
            pass
    if len(parts) == 2 and len(parts[0]) == 4:
        year, month = parts[0], parts[1].zfill(2)
        try:
            y, m = int(year), int(month)
            if 1990 <= y <= 2100 and 1 <= m <= 12:
                return f"{y:04d}-{m:02d}"
        except ValueError:
            pass
    return s  # leave as-is if not parseable


def _iso_week_to_date(year: int, week: int) -> str | None:
    """Convert an ISO year + week-of-year to the Monday of that week."""
    try:
        if year < 1990 or year > 2100:
            return None
        if week < 1 or week > 53:
            return None
        d = date.fromisocalendar(year, week, 1)   # Monday of that ISO week
        return d.isoformat()
    except Exception:
        return None


def extract_production_date_from_outputs(raw_outputs) -> dict:
    """Search a list of raw SSH outputs for any manufacturing/production date.

    Args:
        raw_outputs: iterable of raw strings — typically the outputs of
            `show version`, `show inventory`, `show diag`, and so on.

    Returns:
        {
            "date": "2018-04-15",       # ISO date (YYYY-MM-DD or YYYY-MM)
            "source": "show inventory",  # label of the blob that matched
            "pattern": "Mfg Date",       # which regex matched
            "raw_match": "Mfg Date: 2018-04-15"  # the exact matched substring
        }
        When nothing matches, all fields are empty strings.
    """
    if not raw_outputs:
        return {"date": "", "source": "", "pattern": "", "raw_match": ""}
    # Allow callers to pass either strings or dicts like {"name": "...", "output": "..."}
    blobs = []
    for item in raw_outputs or []:
        if isinstance(item, str):
            blobs.append(("", item))
        elif isinstance(item, dict):
            name = item.get("name") or item.get("source") or ""
            out = item.get("output") or item.get("raw") or ""
            if out:
                blobs.append((str(name), str(out)))
    # Prefer more specific patterns first (already sorted in _PRODUCTION_DATE_PATTERNS).
    for source_label, blob in blobs:
        if not blob:
            continue
        # First sweep for the explicit-date patterns (YY + WW handled separately).
        for idx, (label, pattern) in enumerate(_PRODUCTION_DATE_PATTERNS):
            if label.endswith("(YYWW)"):
                continue
            m = re.search(pattern, blob, re.IGNORECASE)
            if not m:
                continue
            raw_date = m.group(1)
            normalized = _normalize_date_text(raw_date)
            if not normalized:
                continue
            return {
                "date": normalized,
                "source": source_label or label,
                "pattern": label,
                "raw_match": m.group(0).strip(),
            }
        # Second sweep for YYWW (Cisco linecard Mfg Data)
        for label, pattern in _PRODUCTION_DATE_PATTERNS:
            if not label.endswith("(YYWW)"):
                continue
            m = re.search(pattern, blob, re.IGNORECASE)
            if not m:
                continue
            try:
                year_raw = int(m.group(1))
                ww = int(m.group(2))
            except (ValueError, IndexError):
                continue
            # 4-digit year is used directly; 2-digit year is mapped: < 70
            # -> 21st century, >= 70 -> 20th century.
            if year_raw >= 100:
                year = year_raw
            else:
                year = 2000 + year_raw if year_raw < 70 else 1900 + year_raw
            iso_date = _iso_week_to_date(year, ww)
            if not iso_date:
                continue
            return {
                "date": iso_date,
                "source": source_label or label,
                "pattern": label,
                "raw_match": m.group(0).strip(),
            }
    return {"date": "", "source": "", "pattern": "", "raw_match": ""}
