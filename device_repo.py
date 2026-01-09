# device_repo.py
from typing import List, Tuple
from download.db import database_resource

def search_devices(keyword: str, limit: int = 30) -> List[Tuple[int, str]]:
    """
    模糊搜索设备：返回 [(id, name), ...]
    """
    kw = (keyword or "").strip()
    if not kw:
        return []

    sql = """
    SELECT id, name
    FROM devices
    WHERE deleted_at IS NULL
      AND name LIKE %s
    ORDER BY name
    LIMIT %s
    """
    with database_resource() as cursor:
        cursor.execute(sql, (f"%{kw}%", limit))
        return [(int(r[0]), str(r[1])) for r in cursor.fetchall()]

def get_device_by_exact_name(name: str) -> List[Tuple[int, str]]:
    """
    精确匹配：返回 [(id, name)]（可能 0/1/多条，取决于库里是否重名）
    """
    nm = (name or "").strip()
    if not nm:
        return []

    sql = """
    SELECT id, name
    FROM devices
    WHERE deleted_at IS NULL
      AND name = %s
    LIMIT 10
    """
    with database_resource() as cursor:
        cursor.execute(sql, (nm,))
        return [(int(r[0]), str(r[1])) for r in cursor.fetchall()]
