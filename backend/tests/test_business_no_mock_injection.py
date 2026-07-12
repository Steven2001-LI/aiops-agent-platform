"""测试 BusinessMonitorAgent 不再接受 mock_results / simulate_failure 注入"""
from __future__ import annotations


def test_business_monitor_no_mock_results():
    """源码中不应再有 mock_results 字样"""
    from pathlib import Path
    src = Path(__file__).parents[1] / "app" / "agents" / "business_monitor_agent.py"
    content = src.read_text()
    assert "mock_results" not in content, (
        "mock_results injection should be removed (no placeholders allowed)"
    )


def test_business_monitor_no_simulate_failure():
    """源码中不应再有 simulate_failure 字样"""
    from pathlib import Path
    src = Path(__file__).parents[1] / "app" / "agents" / "business_monitor_agent.py"
    content = src.read_text()
    assert "simulate_failure" not in content, (
        "simulate_failure should be removed (no placeholders allowed)"
    )


def test_routes_no_simulate_failure():
    """routes.py 中不应再有 simulate_failure 字样"""
    from pathlib import Path
    src = Path(__file__).parents[1] / "app" / "api" / "routes.py"
    content = src.read_text()
    assert "simulate_failure" not in content, (
        "simulate_failure should be removed from routes.py"
    )