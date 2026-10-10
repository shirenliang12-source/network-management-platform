"""Per-device retention configuration; preserve baselines and newest captures."""
import json
from app.models import SystemSetting, ConfigBackup


def policy(db, device_id):
    row = db.get(SystemSetting, f'backup_retention:{device_id}')
    return json.loads(row.value)['keep'] if row else None


def excess(db, device_id, keep):
    if keep == 0:
        return []
    rows = db.query(ConfigBackup).filter_by(device_id=device_id).order_by(ConfigBackup.backup_time.desc(), ConfigBackup.id.desc()).all()
    return [row for row in rows[keep:] if not row.is_baseline]
