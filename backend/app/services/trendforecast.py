"""趋势预测业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services import forecast_pipeline
from app.store import store

MODULE = "trendforecast"
REQUIRED_FIELDS = ["方案编号", "预测指标", "算法模型"]
ALL_FIELDS = ["方案编号", "预测指标", "算法模型", "历史窗口天", "预测窗口天", "置信区间", "季节性开关", "外部因子"]
FIELD_DEFAULTS = {"历史窗口天": "90", "预测窗口天": "14", "置信区间": "95%", "季节性开关": "关", "外部因子": ""}
STATUS_ORDER = ["草稿", "模型评估中", "已部署", "已下线"]
# 状态只能依次评估、部署、下线：每个动作登记允许的来源状态与目标状态。
ACTION_RULES: dict[str, tuple[set[str], str | None]] = {
    "提交模型评估": ({"草稿"}, "模型评估中"),
    "重新训练": ({"模型评估中", "已部署"}, None),
    "部署方案": ({"模型评估中", "已部署"}, "已部署"),
    "下线方案": ({"已部署"}, "已下线"),
}
NEGATIVE_ACTIONS = ["下线方案"]
EDITABLE_STATUSES = {"草稿", "模型评估中"}


class TrendforecastService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("方案编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        # 完整保存窗口与模型参数：历史窗口、预测窗口、置信区间、季节性开关、外部因子一个不落。
        for field in ALL_FIELDS:
            raw = values.get(field)
            text = str(raw).strip() if raw is not None else ""
            entry[field] = text or FIELD_DEFAULTS.get(field, "")
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["已发布版本"] = None
        entry["候选版本"] = None
        rows.append(entry)
        return entry, []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"预测方案 {entry_id} 不存在或已归档"
        if entry.get("status") not in EDITABLE_STATUSES:
            return None, f"方案当前状态为「{entry.get('status')}」，不能一边更新方案一边留下旧大盘曲线；请先下线或重新训练部署新版本"
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field, entry.get(field)) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        for field in ALL_FIELDS:
            if field in values:
                text = str(values.get(field) or "").strip()
                entry[field] = text or FIELD_DEFAULTS.get(field, "")
        return entry, "预测方案参数已保存，历史窗口与预测窗口已完整落库"

    def run_action(self, entry_id: int, action: str, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"预测方案 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于趋势预测可执行范围"
        allowed_from, target = ACTION_RULES[action]
        current = str(entry.get("status") or "")
        if current not in allowed_from:
            return None, f"当前状态「{current}」不允许执行「{action}」，状态只能依次评估、部署、下线"

        values = values or {}
        interrupt_stage = str(values.get("模拟中断阶段") or "").strip() or None

        if action in ("提交模型评估", "重新训练"):
            run, run_message = forecast_pipeline.start_run(entry, interrupt_stage=interrupt_stage)
            if target is not None:
                entry["status"] = target
            entry["pending"] = True
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            return entry, f"预测方案已{action}：{run_message}"

        if action == "部署方案":
            ok, message = forecast_pipeline.deploy_candidate(entry)
            if not ok:
                return None, message
            entry["status"] = "已部署"
            entry["pending"] = True
            entry["abnormal"] = False
            return entry, f"预测方案已部署：{message}"

        # 下线方案
        forecast_pipeline.archive_published(entry)
        entry["status"] = "已下线"
        entry["pending"] = False
        entry["abnormal"] = True
        return entry, "预测方案已下线：已发布版本归档，大盘预测区间已同步清除"

    def list_runs(self, entry_id: int) -> list[dict[str, Any]]:
        return forecast_pipeline.list_runs(entry_id)

    def resume_run(self, entry_id: int, run_id: int) -> tuple[dict[str, Any] | None, str]:
        run, message = forecast_pipeline.resume_run(run_id)
        if run is not None and int(run.get("方案id", 0)) != entry_id:
            return None, f"预测任务 {run_id} 不属于方案 {entry_id}"
        return run, message

    def cancel_run(self, entry_id: int, run_id: int) -> tuple[dict[str, Any] | None, str]:
        run, message = forecast_pipeline.cancel_run(run_id)
        if run is not None and int(run.get("方案id", 0)) != entry_id:
            return None, f"预测任务 {run_id} 不属于方案 {entry_id}"
        return run, message

    def list_datasets(self, entry_id: int) -> list[dict[str, Any]]:
        return forecast_pipeline.list_datasets(entry_id)

    def get_dataset(self, entry_id: int, version: int) -> dict[str, Any] | None:
        return forecast_pipeline.get_dataset(entry_id, version)
