"""M0 冒烟测试：核心包可导入、FastAPI 应用可创建。"""


def test_app_importable():
    """app 包可导入，FastAPI 实例存在。"""
    from app.main import app

    assert app.title == "PodLore"


def test_subpackages_importable():
    """分层目录包均可导入（infra/services/api/schemas）。"""
    import app.api  # noqa: F401
    import app.infra  # noqa: F401
    import app.schemas  # noqa: F401
    import app.services  # noqa: F401


def test_health_endpoint():
    """/health 健康检查返回 200。"""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
