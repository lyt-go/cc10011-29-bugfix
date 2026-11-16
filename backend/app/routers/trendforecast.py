"""趋势预测接口：维护预测方案，覆盖提交模型评估、重新训练、部署方案、下线方案等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services import forecast_pipeline
from app.services.trendforecast import TrendforecastService

router = APIRouter(prefix="/api/trendforecast", tags=["趋势预测"])

service = TrendforecastService()

LIST_FIELDS = ["方案编号", "预测指标", "算法模型", "历史窗口天", "预测窗口天", "置信区间", "季节性开关", "外部因子"]
STATUSES = ["草稿", "模型评估中", "已部署", "已下线"]
STAGES = forecast_pipeline.STAGES


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按方案编号检索"),
    status: str | None = Query(default=None, description="草稿、模型评估中、已部署、已下线"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按方案编号与状态过滤趋势预测列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出趋势预测清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "trendforecast", "total": total, "items": items}


@router.get("/sync-summary")
def sync_summary() -> dict[str, Any]:
    """发布同步视图：运营概览等页面与监控大盘消费同一份已发布预测数据。"""
    return forecast_pipeline.sync_summary()


@router.get("/stages")
def list_stages() -> dict[str, Any]:
    """预测任务的完整阶段序列：构建、环境准备、数据库迁移、清洗、计算、生成。"""
    return {"stages": STAGES}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条预测方案明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"预测方案 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条预测方案，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="预测方案已登记，历史窗口与预测窗口已完整保存", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """保存方案参数：已部署方案不允许直接改参数，避免方案更新而大盘曲线停在旧版本。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条预测方案执行动作；状态只能依次评估、部署、下线，越序动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/runs")
def list_runs(entry_id: int) -> dict[str, Any]:
    """预测任务的阶段进度：只能按完整阶段推进，中断后从最后一个完整阶段续做。"""
    if service.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"预测方案 {entry_id} 不存在或已归档")
    return {"items": service.list_runs(entry_id), "stages": STAGES}


@router.post("/{entry_id}/runs/{run_id}/resume", response_model=ActionResult)
def resume_run(entry_id: int, run_id: int) -> ActionResult:
    """失败续做：从最后一个完整阶段继续执行。"""
    run, message = service.resume_run(entry_id, run_id)
    if run is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=run.get("状态") == "完成", message=message, entry=run)


@router.post("/{entry_id}/runs/{run_id}/cancel", response_model=ActionResult)
def cancel_run(entry_id: int, run_id: int) -> ActionResult:
    """取消未完成的预测任务并释放指标锁。"""
    run, message = service.cancel_run(entry_id, run_id)
    if run is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=run)


@router.get("/{entry_id}/datasets")
def list_datasets(entry_id: int) -> dict[str, Any]:
    """结果数据集版本列表：每次完整运行一个版本，快照可核对，重训不覆盖已发布。"""
    if service.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"预测方案 {entry_id} 不存在或已归档")
    return {"items": service.list_datasets(entry_id)}


@router.get("/{entry_id}/datasets/{version}")
def get_dataset(entry_id: int, version: int) -> dict[str, Any]:
    """单个版本的完整快照：参数、清洗报告、季节性判定、预测区间都可追溯。"""
    dataset = service.get_dataset(entry_id, version)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"预测方案 {entry_id} 的版本 v{version} 不存在")
    return dataset
