"""趋势预测共享链路：历史序列清洗、趋势预测计算、结果数据集与版本发布。

这一层是预测方案与监控大盘共用的数据流：

- 预测任务按完整阶段推进（构建 → 环境准备 → 数据库迁移 → 历史数据清洗 →
  趋势预测计算 → 结果数据集生成），每个阶段落检查点；中断后从最后一个完整
  阶段续做，不会回到半成品。
- 同一指标同一时间只允许一个预测任务执行；争用失败的任务保留检查点，可以
  稍后失败续做。
- 每次完整运行生成一个不可变的结果数据集版本（参数快照 + 预测区间 +
  季节性判定 + 外部因子），重训只产生新的候选版本，绝不覆盖已发布版本。
- 监控大盘只消费「已发布」状态的数据集，保证大盘预测曲线永远只对应一个
  方案版本。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.store import store

MODULE = "trendforecast"
RUN_TABLE = "trendforecast_run"
DATASET_TABLE = "trendforecast_dataset"
LOCK_TABLE = "metric_lock"
HISTORY_TABLE = "metric_history"

# 预测任务必须依次走完的完整阶段；部署前后共用同一发布口径。
STAGES = ["构建", "环境准备", "数据库迁移", "历史数据清洗", "趋势预测计算", "结果数据集生成"]

DATASET_STATUS = ["候选", "已发布", "已归档"]

_Z_VALUES = {"80%": 1.28, "90%": 1.645, "95%": 1.96, "99%": 2.576}
_FACTOR_UPLIFT = 0.015  # 每个外部因子对预测值的确定性抬升，保证结果可复现


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _parse_factors(raw: Any) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    for sep in ["，", "、", ";", "；", " "]:
        text = text.replace(sep, ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def _metric_series(metric: str) -> list[tuple[date, float | None]]:
    series: list[tuple[date, float | None]] = []
    for row in store.internal_rows(HISTORY_TABLE):
        if row.get("指标编号") != metric:
            continue
        series.append((date.fromisoformat(str(row["日期"])), row.get("值")))
    series.sort(key=lambda item: item[0])
    return series


def _clean_history(metric: str, window_days: int) -> tuple[list[tuple[date, float]], dict[str, Any]]:
    """历史数据清洗：截取历史窗口、剔除缺失点、按 IQR 剔除离群点。"""
    series = _metric_series(metric)
    if not series:
        return [], {"指标编号": metric, "窗口天": window_days, "原始点数": 0, "缺失剔除": 0, "离群剔除": 0}
    end = series[-1][0]
    start = end - timedelta(days=max(window_days, 1) - 1)
    windowed = [(day, value) for day, value in series if start <= day <= end]
    missing = sum(1 for _, value in windowed if value is None)
    present = [(day, float(value)) for day, value in windowed if value is not None]

    values = sorted(value for _, value in present)
    q1 = values[len(values) // 4]
    q3 = values[(len(values) * 3) // 4]
    iqr = q3 - q1
    low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    cleaned = [(day, value) for day, value in present if low <= value <= high]
    outliers = len(present) - len(cleaned)

    report = {
        "指标编号": metric,
        "窗口天": window_days,
        "窗口起": start.isoformat(),
        "窗口止": end.isoformat(),
        "原始点数": len(windowed),
        "缺失剔除": missing,
        "离群剔除": outliers,
        "有效点数": len(cleaned),
    }
    return cleaned, report


def _least_squares(points: list[tuple[date, float]]) -> tuple[float, float, float]:
    """拟合 y = a + b*x，返回截距、斜率与残差标准差。"""
    n = len(points)
    xs = list(range(n))
    ys = [value for _, value in points]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    var_x = sum((x - mean_x) ** 2 for x in xs) or 1.0
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = cov / var_x
    intercept = mean_y - slope * mean_x
    residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    std = (sum(r * r for r in residuals) / max(n - 2, 1)) ** 0.5
    return intercept, slope, std


def _seasonality(points: list[tuple[date, float]], intercept: float, slope: float, enabled: bool) -> tuple[dict[int, float], dict[str, Any]]:
    """季节性判定：按星期归组残差均值，输出可追溯的判定依据。"""
    if not enabled:
        return {}, {"启用": False, "周期": None, "强度": 0.0, "判定依据": "方案季节性开关为关，未启用季节分量"}
    buckets: dict[int, list[float]] = {}
    for index, (day, value) in enumerate(points):
        residual = value - (intercept + slope * index)
        buckets.setdefault(day.weekday(), []).append(residual)
    indices = {dow: sum(vals) / len(vals) for dow, vals in buckets.items()}
    mean_level = sum(value for _, value in points) / len(points)
    strength = max((abs(v) for v in indices.values()), default=0.0) / (abs(mean_level) or 1.0)
    judgement = {
        "启用": True,
        "周期": "7天（按星期）",
        "强度": round(strength, 4),
        "判定依据": "对清洗后残差按星期归组取均值，得到 7 个季节指数",
    }
    return indices, judgement


def _forecast(plan: dict[str, Any], cleaned: list[tuple[date, float]]) -> dict[str, Any]:
    """趋势预测计算：线性趋势 + 可选季节分量 + 外部因子抬升 + 置信区间。"""
    horizon = max(_parse_int(plan.get("预测窗口天"), 14), 1)
    confidence = str(plan.get("置信区间") or "95%").strip()
    z = _Z_VALUES.get(confidence, 1.96)
    factors = _parse_factors(plan.get("外部因子"))
    seasonal_on = str(plan.get("季节性开关") or "关").strip() == "开"

    intercept, slope, std = _least_squares(cleaned)
    seasonal_indices, judgement = _seasonality(cleaned, intercept, slope, seasonal_on)
    uplift = 1 + _FACTOR_UPLIFT * len(factors)

    last_day = cleaned[-1][0]
    n = len(cleaned)
    interval: list[dict[str, Any]] = []
    for step in range(1, horizon + 1):
        day = last_day + timedelta(days=step)
        base = intercept + slope * (n - 1 + step)
        base += seasonal_indices.get(day.weekday(), 0.0)
        point = base * uplift
        spread = z * std * (step ** 0.5)
        interval.append({
            "日期": day.isoformat(),
            "预测值": round(point, 2),
            "下限": round(point - spread, 2),
            "上限": round(point + spread, 2),
        })
    return {
        "预测区间": interval,
        "季节性判定": judgement,
        "外部因子": factors,
        "趋势斜率": round(slope, 4),
        "残差标准差": round(std, 4),
        "置信区间": confidence,
    }


def _next_id(table: str) -> int:
    return max((int(row.get("id", 0)) for row in store.internal_rows(table)), default=0) + 1


def _next_version(plan_id: int) -> int:
    versions = [int(row.get("版本号", 0)) for row in store.internal_rows(DATASET_TABLE) if row.get("方案id") == plan_id]
    versions += [int(row.get("版本号", 0)) for row in store.internal_rows(RUN_TABLE) if row.get("方案id") == plan_id]
    return max(versions, default=0) + 1


def _lock_for(metric: str) -> dict[str, Any] | None:
    for row in store.internal_rows(LOCK_TABLE):
        if row.get("指标编号") == metric:
            return row
    return None


def _acquire_lock(metric: str, run_id: int, plan_id: int) -> None:
    store.internal_rows(LOCK_TABLE).append({"指标编号": metric, "运行id": run_id, "方案id": plan_id, "加锁时间": _now()})


def _release_lock(metric: str, run_id: int) -> None:
    locks = store.internal_rows(LOCK_TABLE)
    locks[:] = [row for row in locks if not (row.get("指标编号") == metric and row.get("运行id") == run_id)]


def _new_run(plan: dict[str, Any]) -> dict[str, Any]:
    run = {
        "id": _next_id(RUN_TABLE),
        "方案id": int(plan["id"]),
        "方案编号": plan.get("方案编号"),
        "预测指标": plan.get("预测指标"),
        "版本号": _next_version(int(plan["id"])),
        "阶段": list(STAGES),
        "已完成阶段": [],
        "状态": "进行中",
        "失败原因": "",
        "清洗报告": None,
        "创建时间": _now(),
        "更新时间": _now(),
    }
    store.internal_rows(RUN_TABLE).append(run)
    return run


def _fail_run(run: dict[str, Any], reason: str) -> None:
    run["状态"] = "失败"
    run["失败原因"] = reason
    run["更新时间"] = _now()


def _execute_stages(plan: dict[str, Any], run: dict[str, Any], interrupt_stage: str | None) -> None:
    """从最后一个完整阶段之后继续推进；任一阶段中断都保留检查点。"""
    metric = str(plan.get("预测指标") or "")
    done: list[str] = run["已完成阶段"]
    for stage in STAGES[len(done):]:
        if interrupt_stage and stage == interrupt_stage:
            _fail_run(run, f"阶段「{stage}」执行中断，可从最后一个完整阶段续做")
            return
        if stage == "历史数据清洗":
            window = max(_parse_int(plan.get("历史窗口天"), 90), 1)
            cleaned, report = _clean_history(metric, window)
            if len(cleaned) < 8:
                _fail_run(run, f"指标 {metric} 清洗后有效点不足（{len(cleaned)} 个），无法计算趋势预测")
                return
            run["清洗报告"] = report
            run["_清洗结果"] = cleaned
        elif stage == "趋势预测计算":
            cleaned = run.get("_清洗结果")
            if cleaned is None:
                window = max(_parse_int(plan.get("历史窗口天"), 90), 1)
                cleaned, _ = _clean_history(metric, window)
            run["_预测结果"] = _forecast(plan, cleaned)
        elif stage == "结果数据集生成":
            result = run.get("_预测结果")
            if result is None:
                _fail_run(run, "趋势预测计算尚未完成，不能生成结果数据集")
                return
            dataset = {
                "id": _next_id(DATASET_TABLE),
                "方案id": int(plan["id"]),
                "方案编号": plan.get("方案编号"),
                "预测指标": metric,
                "版本号": run["版本号"],
                "状态": "候选",
                "参数快照": {field: plan.get(field) for field in ["算法模型", "历史窗口天", "预测窗口天", "置信区间", "季节性开关", "外部因子"]},
                "清洗报告": run.get("清洗报告"),
                "季节性判定": result["季节性判定"],
                "外部因子": result["外部因子"],
                "预测区间": result["预测区间"],
                "趋势斜率": result["趋势斜率"],
                "残差标准差": result["残差标准差"],
                "运行id": run["id"],
                "生成时间": _now(),
            }
            store.internal_rows(DATASET_TABLE).append(dataset)
            plan["候选版本"] = run["版本号"]
        done.append(stage)
        run["更新时间"] = _now()
    run["状态"] = "完成"
    run["失败原因"] = ""
    run.pop("_清洗结果", None)
    run.pop("_预测结果", None)


def start_run(plan: dict[str, Any], interrupt_stage: str | None = None) -> tuple[dict[str, Any], str]:
    """启动一次预测任务；指标被占用时本次任务直接失败，但保留可续做的运行记录。"""
    metric = str(plan.get("预测指标") or "")
    run = _new_run(plan)
    holder = _lock_for(metric)
    if holder is not None:
        _fail_run(run, f"指标 {metric} 已有预测任务在执行（运行#{holder['运行id']}），本次任务未启动，可稍后失败续做")
        return run, run["失败原因"]
    _acquire_lock(metric, run["id"], int(plan["id"]))
    _execute_stages(plan, run, interrupt_stage)
    if run["状态"] == "完成":
        _release_lock(metric, run["id"])
        return run, f"预测任务完成，已生成候选版本 v{run['版本号']}"
    return run, run["失败原因"]


def resume_run(run_id: int) -> tuple[dict[str, Any] | None, str]:
    """失败续做：从最后一个完整阶段继续，不回退到半成品。"""
    run = next((row for row in store.internal_rows(RUN_TABLE) if int(row.get("id", 0)) == run_id), None)
    if run is None:
        return None, f"预测任务 {run_id} 不存在"
    if run["状态"] == "完成":
        return run, f"预测任务 {run_id} 已完成，无需续做"
    if run["状态"] == "已取消":
        return run, f"预测任务 {run_id} 已取消，不能续做"
    plan = store.find(MODULE, int(run["方案id"]))
    if plan is None:
        return None, f"预测方案 {run['方案id']} 不存在或已归档"
    metric = str(run.get("预测指标") or "")
    holder = _lock_for(metric)
    if holder is not None and int(holder["运行id"]) != run_id:
        _fail_run(run, f"指标 {metric} 仍被运行#{holder['运行id']} 占用，续做失败，可稍后重试")
        return run, run["失败原因"]
    if holder is None:
        _acquire_lock(metric, run_id, int(run["方案id"]))
    run["状态"] = "进行中"
    _execute_stages(plan, run, None)
    if run["状态"] == "完成":
        _release_lock(metric, run_id)
        return run, f"预测任务续做完成，已生成候选版本 v{run['版本号']}"
    return run, run["失败原因"]


def cancel_run(run_id: int) -> tuple[dict[str, Any] | None, str]:
    """取消未完成的任务并释放指标锁，让后续任务可以启动。"""
    run = next((row for row in store.internal_rows(RUN_TABLE) if int(row.get("id", 0)) == run_id), None)
    if run is None:
        return None, f"预测任务 {run_id} 不存在"
    if run["状态"] in ("完成", "已取消"):
        return run, f"预测任务 {run_id} 已结束，无需取消"
    run["状态"] = "已取消"
    run["更新时间"] = _now()
    _release_lock(str(run.get("预测指标") or ""), run_id)
    return run, f"预测任务 {run_id} 已取消，指标锁已释放"


def list_runs(plan_id: int) -> list[dict[str, Any]]:
    runs = [dict(row) for row in store.internal_rows(RUN_TABLE) if row.get("方案id") == plan_id]
    for run in runs:
        run.pop("_清洗结果", None)
        run.pop("_预测结果", None)
    runs.sort(key=lambda row: int(row.get("id", 0)))
    return runs


def list_datasets(plan_id: int) -> list[dict[str, Any]]:
    datasets = [row for row in store.internal_rows(DATASET_TABLE) if row.get("方案id") == plan_id]
    return sorted(datasets, key=lambda row: int(row.get("版本号", 0)))


def get_dataset(plan_id: int, version: int) -> dict[str, Any] | None:
    for row in store.internal_rows(DATASET_TABLE):
        if row.get("方案id") == plan_id and int(row.get("版本号", 0)) == version:
            return row
    return None


def published_for_plan(plan_id: int) -> dict[str, Any] | None:
    for row in store.internal_rows(DATASET_TABLE):
        if row.get("方案id") == plan_id and row.get("状态") == "已发布":
            return row
    return None


def published_for_metric(metric: str) -> dict[str, Any] | None:
    for row in store.internal_rows(DATASET_TABLE):
        if row.get("预测指标") == metric and row.get("状态") == "已发布":
            return row
    return None


def deploy_candidate(plan: dict[str, Any]) -> tuple[bool, str]:
    """发布候选版本：同一指标的大盘曲线只对应一个方案版本，原子切换。"""
    plan_id = int(plan["id"])
    candidate_version = plan.get("候选版本")
    if candidate_version is None:
        return False, "没有已完成模型评估的候选版本，请先执行训练"
    candidate = get_dataset(plan_id, int(candidate_version))
    if candidate is None or candidate.get("状态") != "候选":
        return False, f"候选版本 v{candidate_version} 不存在或已发布过，请重新训练"
    if plan.get("已发布版本") is not None and int(plan["已发布版本"]) == int(candidate_version):
        return False, f"版本 v{candidate_version} 已在线上，没有待发布的新版本"
    metric = str(plan.get("预测指标") or "")
    other = published_for_metric(metric)
    if other is not None and int(other["方案id"]) != plan_id:
        return False, f"指标 {metric} 的预测曲线正由方案 {other['方案编号']} v{other['版本号']} 发布，请先下线对应方案"
    current = published_for_plan(plan_id)
    if current is not None:
        current["状态"] = "已归档"
    candidate["状态"] = "已发布"
    candidate["发布时间"] = _now()
    plan["已发布版本"] = int(candidate["版本号"])
    return True, f"版本 v{candidate['版本号']} 已发布，大盘预测曲线已同步切换"


def archive_published(plan: dict[str, Any]) -> None:
    """下线：归档已发布数据集，大盘预测区间随状态同步消失。"""
    current = published_for_plan(int(plan["id"]))
    if current is not None:
        current["状态"] = "已归档"
        current["归档时间"] = _now()
    plan["已发布版本"] = None


def forecast_overlay(metrics: list[str]) -> list[dict[str, Any]]:
    """监控大盘消费的共享数据：每个指标只取已发布的那一个版本。"""
    overlay: list[dict[str, Any]] = []
    for metric in metrics:
        published = published_for_metric(metric)
        if published is None:
            overlay.append({"指标编号": metric, "已发布": False})
            continue
        overlay.append({
            "指标编号": metric,
            "已发布": True,
            "方案编号": published["方案编号"],
            "版本号": published["版本号"],
            "季节性判定": published["季节性判定"],
            "外部因子": published["外部因子"],
            "预测区间": published["预测区间"],
            "参数快照": published["参数快照"],
            "发布时间": published.get("发布时间"),
        })
    return overlay


def sync_summary() -> dict[str, Any]:
    """给运营概览等页面消费的发布同步视图：只含已发布版本。"""
    published = [row for row in store.internal_rows(DATASET_TABLE) if row.get("状态") == "已发布"]
    dashboards = store.rows("dashboard")
    items: list[dict[str, Any]] = []
    for row in sorted(published, key=lambda item: str(item.get("方案编号") or "")):
        interval = row.get("预测区间") or []
        covered = [dash.get("大盘编号") for dash in dashboards if row["预测指标"] in str(dash.get("包含指标组") or "")]
        items.append({
            "方案编号": row["方案编号"],
            "预测指标": row["预测指标"],
            "已发布版本": row["版本号"],
            "预测区间起": interval[0]["日期"] if interval else None,
            "预测区间止": interval[-1]["日期"] if interval else None,
            "季节性启用": (row.get("季节性判定") or {}).get("启用"),
            "外部因子": row.get("外部因子") or [],
            "覆盖大盘": covered,
            "发布时间": row.get("发布时间"),
        })
    return {
        "已发布预测数": len(items),
        "覆盖指标数": len({item["预测指标"] for item in items}),
        "覆盖大盘数": len({dash for item in items for dash in item["覆盖大盘"]}),
        "items": items,
    }


def bootstrap() -> None:
    """启动引导：为种子数据里已部署的方案补齐已发布数据集。

    种子方案标记为「已部署」但还没有结果数据集；这里走与线上一致的完整
    阶段流水线生成 v1 并发布，保证部署前后共用同一发布口径，也让监控大盘
    起服务后即可消费到预测区间。
    """
    for plan in store.rows(MODULE):
        if plan.get("status") != "已部署":
            continue
        if published_for_plan(int(plan["id"])) is not None:
            continue
        run, _ = start_run(plan)
        if run["状态"] != "完成":
            resume_run(int(run["id"]))
        deploy_candidate(plan)
