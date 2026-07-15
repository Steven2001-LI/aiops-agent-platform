# Accepted Baseline v1

- 生成时间:2026-07-15T14:03:43.265438+00:00
- 被评测代码:`98be186eab7b9992ccc60ec534c13ac260b62273`(生成时工作树含未提交改动)
- 数据集:app.data.datasets.FAULT_SCENARIOS (golden/fault_scenarios.json),参评 11/15 条(排除 fs_biz_007, fs_biz_008, fs_biz_009, fs_biz_010:业务信号场景无告警指标,不走告警驱动 RCA 路径)
- 口径:纯规则路径(LLM 全关)、记忆隔离、reasoning 维度规则指标

## 聚合指标

- 根因 Top-1 准确率:0.0000
- 根因 Top-3 命中率:0.0909
- 建议操作准确率:0.1818
- 证据完整性:1.0000
- 置信度校准误差:0.2328
- 平均置信度:0.2328

## 逐场景结果

| 场景 | 服务 | 预测根因 | 真值根因 | Top-1 | Top-3 | 动作命中 |
|------|------|----------|----------|-------|-------|----------|
| fs_001 | order-service | traffic_spike | recent_deployment | ✗ | ✓ | ✓ |
| fs_002 | payment-service | resource_exhaustion | memory_leak | ✗ | ✗ | ✓ |
| fs_003 | user-service | dependency_failure | database_connection_pool_exhausted | ✗ | ✗ | ✗ |
| fs_004 | inventory-service | dependency_failure | cache_failure | ✗ | ✗ | ✗ |
| fs_005 | api-gateway | upstream_service_down | configuration_error | ✗ | ✗ | ✗ |
| fs_biz_001 | payment-service | dependency_failure | third_party_timeout | ✗ | ✗ | ✗ |
| fs_biz_002 | order-service | dependency_failure | inventory_data_stale | ✗ | ✗ | ✗ |
| fs_biz_003 | user-service | dependency_failure | token_cache_expired | ✗ | ✗ | ✗ |
| fs_biz_004 | api-gateway | dependency_failure | rate_limit_misconfig | ✗ | ✗ | ✗ |
| fs_biz_005 | order-service | dependency_failure | corrupted_message | ✗ | ✗ | ✗ |
| fs_biz_006 | inventory-service | dependency_failure | replication_lag | ✗ | ✗ | ✗ |

结果为确定性双跑一致校验后如实入库,未做任何挑选;该基线是合成场景上的确定性参照点,不代表真实生产效果。

复现:`cd backend && python -m scripts.generate_accepted_baseline`(结果哈希 `4d7a75a64075040d…`,由 tests/test_accepted_baseline.py 守护)
