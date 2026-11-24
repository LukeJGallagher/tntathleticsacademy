"""Full-database backup and restore as a ZIP of JSON tables. Works on SQLite, MySQL and Postgres.

Restore replaces everything. It runs in one transaction, so a bad file leaves the data untouched.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime

from sqlalchemy import delete, insert, select, text

from db import Base

FORMAT_VERSION = 1


def _encode(v):
    if isinstance(v, datetime):
        return {"__dt": v.isoformat()}
    if isinstance(v, date):
        return {"__d": v.isoformat()}
    return v


def _decode(v):
    if isinstance(v, dict):
        if "__dt" in v:
            return datetime.fromisoformat(v["__dt"])
        if "__d" in v:
            return date.fromisoformat(v["__d"])
    return v


def make_backup(s) -> bytes:
    buf = io.BytesIO()
    counts = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for table in Base.metadata.sorted_tables:
            rows = [{k: _encode(v) for k, v in r._mapping.items()} for r in s.execute(select(table))]
            counts[table.name] = len(rows)
            z.writestr(f"{table.name}.json", json.dumps(rows, ensure_ascii=False))
        z.writestr("manifest.json", json.dumps({"format": FORMAT_VERSION, "created": datetime.now().isoformat(),
                                                 "tables": counts}, indent=2))
    return buf.getvalue()


def read_manifest(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return json.loads(z.read("manifest.json"))


def restore_backup(s, data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        manifest = json.loads(z.read("manifest.json"))
        if manifest.get("format") != FORMAT_VERSION:
            raise ValueError("Unrecognised backup format.")
        names = set(z.namelist())
        listed = manifest.get("tables", {})
        missing = [t for t in listed if f"{t}.json" not in names]
        if missing or "users" not in listed:
            raise ValueError(f"Backup is incomplete (missing: {', '.join(missing) or 'users'}). Nothing was changed.")
        # Tables newer than the backup (not listed) simply restore empty.
        payload = {t.name: json.loads(z.read(f"{t.name}.json")) if t.name in listed else []
                   for t in Base.metadata.sorted_tables}
    if not any(u.get("role") == "admin" and u.get("active") for u in payload["users"]):
        raise ValueError("Backup has no active admin login, so restoring it would lock everyone out. Nothing was changed.")
    for table in reversed(Base.metadata.sorted_tables):
        s.execute(delete(table))
    for table in Base.metadata.sorted_tables:
        cols = {c.name for c in table.columns}
        rows = [{k: _decode(v) for k, v in r.items() if k in cols} for r in payload[table.name]]
        if rows:
            s.execute(insert(table), rows)
    s.flush()
    if s.get_bind().dialect.name == "postgresql":  # explicit ids don't advance Postgres sequences
        for table in Base.metadata.sorted_tables:
            if "id" in table.columns and table.c.id.autoincrement in (True, "auto") and table.c.id.primary_key:
                s.execute(text(f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                               f"COALESCE((SELECT MAX(id) FROM {table.name}), 0) + 1, false)"))
    return listed
