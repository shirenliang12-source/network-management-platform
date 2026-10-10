"""Verified copy from a stopped, upgraded SQLite snapshot to an empty PG schema.

This module does not change the application's active database or remove SQLite.
The installer must retain the data directory/secret key and switch only after success.
"""
import hashlib
import json
from datetime import date, datetime
from sqlalchemy import MetaData, select, func, text


def _canonical(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {'bytes_hex': value.hex()}
    return value


def _digest(connection, table):
    digest = hashlib.sha256()
    count = 0
    for row in connection.execute(select(table).order_by(*table.primary_key.columns)).mappings():
        payload = json.dumps(dict(row), default=_canonical, sort_keys=True,
                             ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        digest.update(len(payload).to_bytes(8, 'big')); digest.update(payload)
        count += 1
    return count, digest.hexdigest()


def copy_verified_snapshot(source_engine, target_engine):
    """Require identical migrated schemas and an empty target; fail atomically."""
    if source_engine.dialect.name != 'sqlite' or target_engine.dialect.name != 'postgresql':
        raise ValueError('迁移仅支持 SQLite 快照 → PostgreSQL')
    metadata = MetaData()
    metadata.reflect(bind=target_engine)
    source_metadata = MetaData()
    source_metadata.reflect(bind=source_engine)
    if set(metadata.tables) != set(source_metadata.tables):
        raise ValueError('源与目标表结构不一致，必须先在 SQLite 快照上完成版本升级')
    for name, table in metadata.tables.items():
        if set(table.columns.keys()) != set(source_metadata.tables[name].columns.keys()):
            raise ValueError(f'表 {name} 字段不一致，迁移取消')
        if not table.primary_key.columns:
            raise ValueError(f'表 {name} 缺少主键，无法校验')
    if 'alembic_version' not in metadata.tables:
        raise ValueError('缺少迁移版本标识')
    report = {}
    with source_engine.connect() as source, target_engine.begin() as target:
        # Hold a consistent SQLite read snapshot for the entire copy.
        source.exec_driver_sql('BEGIN')
        if source.exec_driver_sql('PRAGMA integrity_check').scalar() != 'ok':
            raise ValueError('SQLite 完整性检查失败')
        if source.exec_driver_sql('PRAGMA foreign_key_check').fetchone():
            raise ValueError('SQLite 存在无效外键，禁止迁移')
        versions = metadata.tables['alembic_version']
        if list(source.execute(select(versions))) != list(target.execute(select(versions))):
            raise ValueError('源与目标迁移版本不一致')
        tables = [t for t in metadata.sorted_tables if t.name != 'alembic_version']
        for table in tables:
            quoted = target.dialect.identifier_preparer.quote(table.name)
            target.exec_driver_sql(f'LOCK TABLE {quoted} IN ACCESS EXCLUSIVE MODE')
            if target.execute(select(func.count()).select_from(table)).scalar():
                raise ValueError('目标 PG 库已有业务数据，禁止覆盖或混合迁移')
        for table in tables:
            # Use target SQLAlchemy types to decode SQLite JSON/datetime/boolean.
            self_columns = {fk.parent.name for fk in table.foreign_keys if fk.column.table.name == table.name}
            if any(not table.c[name].nullable for name in self_columns):
                raise ValueError(f'表 {table.name} 存在不可空自关联，暂不支持迁移')
            deferred = []
            result = source.execute(select(table).order_by(*table.primary_key.columns)).mappings()
            while True:
                rows = [dict(row) for row in result.fetchmany(250)]
                if not rows:
                    break
                for row in rows:
                    if self_columns:
                        deferred.append(({c.name: row[c.name] for c in table.primary_key.columns},
                                         {name: row[name] for name in self_columns}))
                        for name in self_columns:
                            row[name] = None
                target.execute(table.insert(), rows)
            for primary, values in deferred:
                statement = table.update()
                for name, value in primary.items():
                    statement = statement.where(table.c[name] == value)
                target.execute(statement.values(**values))
            expected = _digest(source, table)
            actual = _digest(target, table)
            if actual != expected:
                raise ValueError(f'表 {table.name} 数据校验不一致，已取消迁移')
            report[table.name] = {'rows': actual[0], 'sha256': actual[1]}
            for column in table.primary_key.columns:
                sequence = target.execute(text('SELECT pg_get_serial_sequence(:table_name, :column_name)'),
                                          {'table_name': table.name, 'column_name': column.name}).scalar()
                if sequence:
                    maximum = target.execute(select(func.max(column))).scalar()
                    target.execute(text('SELECT setval(CAST(:seq AS regclass), :value, :called)'),
                                   {'seq': sequence, 'value': max(1, maximum or 1), 'called': maximum is not None})
    return {'ok': True, 'tables': report, 'rows': sum(v['rows'] for v in report.values())}
