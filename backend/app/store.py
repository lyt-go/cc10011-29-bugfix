"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

业务表（SEED_ROWS）会出现在运营概览里；内部表（指标历史序列、预测运行、
预测结果数据集、指标任务锁）只服务趋势预测链路，不进概览统计，保持原有
数据库结构兼容——已有表一行不动，新结构全部以追加方式注册。
"""
from __future__ import annotations

from typing import Any

from app.seed import METRIC_HISTORY_ROWS, SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._internal: dict[str, list[dict[str, Any]]] = {
            "metric_history": [dict(row) for row in METRIC_HISTORY_ROWS],
            "trendforecast_run": [],
            "trendforecast_dataset": [],
            "metric_lock": [],
        }

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def internal_rows(self, name: str) -> list[dict[str, Any]]:
        """趋势预测链路专用的内部表：不参与概览统计，结构只增不改。"""
        return self._internal.setdefault(name, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
