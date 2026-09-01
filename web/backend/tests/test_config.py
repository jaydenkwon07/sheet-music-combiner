import importlib

from web.backend import config


def test_defaults(monkeypatch, tmp_path):
    for var in ("SMC_SESSION_ROOT", "SMC_SESSION_TTL", "SMC_SESSION_SWEEP_INTERVAL",
                "SMC_MAX_UPLOAD", "SMC_CORS_ORIGINS", "SMC_HOST", "SMC_PORT"):
        monkeypatch.delenv(var, raising=False)
    importlib.reload(config)
    s = config.get_settings()
    assert s.session_ttl_seconds == 3600
    assert s.session_sweep_interval_seconds == 900
    assert s.cors_origins == ["http://localhost:5173"]
    assert s.host == "127.0.0.1"
    assert s.port == 8000


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("SMC_SESSION_TTL", "120")
    monkeypatch.setenv("SMC_SESSION_SWEEP_INTERVAL", "30")
    monkeypatch.setenv("SMC_CORS_ORIGINS", "http://a.com,http://b.com")
    monkeypatch.setenv("SMC_PORT", "9001")
    importlib.reload(config)
    s = config.get_settings()
    assert s.session_ttl_seconds == 120
    assert s.session_sweep_interval_seconds == 30
    assert s.cors_origins == ["http://a.com", "http://b.com"]
    assert s.port == 9001
