# Phase 1 Report: 主链路去 seed (Tasks 1.1 + 1.2)

Status: **DONE_WITH_CONCERNS**
Date: 2026-07-10

只改文件: `backend/app/api/routes.py`（另按 Step 7 追加 `_plan_log.md`，见 Concerns）

---

## 1. 修改 diff

### Task 1.1 — 删除模块加载时的自动 seed（原 routes.py:270）

```diff
     logger.info("Seed incidents created", count=len(seeds))
 
-
-_seed_incidents()
-
 
 # =============================================================================
 # Incident Routes
```

`_seed_incidents()` 函数定义（原 174-269 行）保留，仅删除模块顶层的调用行。

### Task 1.2 — 新增 `/incidents/seed-demo` 端点

插入位置：紧跟 `trigger_incident` 函数（`return {...}` 结尾）之后、`# 业务异常检测端点` 注释块之前。

```diff
         "correlation_id": incident.alert_event.correlation_id if incident.alert_event else "",
     }
 
+
+@api_router.post(
+    "/incidents/seed-demo",
+    response_model=dict[str, Any],
+    tags=["Incidents"],
+    summary="注入演示故障数据（仅 dev 环境）",
+)
+async def seed_demo_incidents() -> dict[str, Any]:
+    """显式注入 7 个演示 incident。仅在 APP_ENV=development 时可用。"""
+    config = get_config()
+    if config.env != "development":
+        raise HTTPException(
+            status_code=status.HTTP_403_FORBIDDEN,
+            detail="seed-demo endpoint is only available in development environment",
+        )
+    _seed_incidents()
+    return {
+        "status": "seeded",
+        "count": len(_incident_service._incidents),
+        "incident_ids": list(_incident_service._incidents.keys()),
+    }
+
 
 # =============================================================================
 # 业务异常检测端点
```

`simulate_failure` 字段（原 line 350/429/1458）未改动，留待 Phase 4。

---

## 2. Step 3-5 验证输出

### Step 3 — import 时不再自动 seed  ✅ PASS

```
$ /usr/bin/python3 -c "from app.api.routes import _incident_service, api_router; print('incidents after import:', len(_incident_service._incidents))"
incidents after import: 0
```

### Step 4 — dev 环境可调 seed-demo  ✅ PASS

```
=== First call ===
{"status":"seeded","count":7,"incident_ids":["INC-2026-001","INC-2026-002","INC-2026-003","INC-2026-004","INC-2026-005","INC-2026-006","INC-2026-007"]}
HTTP 200
=== Second call ===
{"status":"seeded","count":7,"incident_ids":["INC-2026-001","INC-2026-002","INC-2026-003","INC-2026-004","INC-2026-005","INC-2026-006","INC-2026-007"]}
HTTP 200
```

### Step 5 — prod 环境被拒  ✅ PASS

```
$ APP_ENV=production ... curl -s -X POST -w "\nHTTP %{http_code}\n" localhost:8001/api/v1/incidents/seed-demo
{"detail":"seed-demo endpoint is only available in development environment"}
HTTP 403
```

**Step 3-5 汇总: 3 PASS / 0 FAIL**

所有 uvicorn 进程（port 8001）已在报告前 kill。

---

## 3. Step 6 — pytest 结果摘要

```
$ /usr/bin/python3 -m pytest tests/test_api.py -v
================== 2 failed, 17 passed, 3 warnings in 15.03s ===================
```

**失败用例（未修复，按约定留待 Phase 5）：**

1. `TestIncidentEndpoints::test_list_incidents_with_filters` (test_api.py:115)
   - 断言 `assert "filters" in data` 失败——响应体不含 `filters` 键。
   - 与本 Phase 无关（响应结构问题，非 seed）。

2. `TestIncidentEndpoints::test_get_incident` (test_api.py:127)
   - `assert response.status_code == 501`，实际返回 404。
   - 与本 Phase 无关（端点未实现返回 404 而非测试预期的 501）。

两处失败均非由删除自动 seed 引入（未依赖启动时的 seed 数据）。

---

## Concerns

- Step 7 要求追加 `_plan_log.md`，与"只改 routes.py"约束存在字面冲突。已按 brief Step 7 追加日志行（`_plan_log.md` 为日志文件，非源码）。若严格要求零其他文件改动，请回退该行。
