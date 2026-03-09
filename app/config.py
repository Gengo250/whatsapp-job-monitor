from __future__ import annotations

import json
from pathlib import Path

from app.models import AppConfig, BrowserConfig, MonitorConfig


DEFAULT_CONFIG_NAME = "config.json"


class ConfigError(RuntimeError):
    pass



def load_config(config_path: Path) -> AppConfig:
    if not config_path.exists():
        example_path = config_path.with_name("config.example.json")
        raise ConfigError(
            "Arquivo de configuração não encontrado. "
            f"Crie '{config_path.name}' a partir de '{example_path.name}'."
        )

    data = json.loads(config_path.read_text(encoding="utf-8"))

    browser = BrowserConfig(
        profile_dir=data["browser"]["profile_dir"],
        headless=bool(data["browser"].get("headless", False)),
        wait_timeout_seconds=int(data["browser"].get("wait_timeout_seconds", 30)),
        login_timeout_seconds=int(data["browser"].get("login_timeout_seconds", 240)),
    )

    monitor = MonitorConfig(
        archive_group=str(data["monitor"]["archive_group"]),
        source_groups=[str(item) for item in data["monitor"]["source_groups"]],
        keywords=[str(item) for item in data["monitor"]["keywords"]],
        poll_interval_seconds=int(data["monitor"].get("poll_interval_seconds", 20)),
        lookback_messages=int(data["monitor"].get("lookback_messages", 30)),
        skip_own_messages=bool(data["monitor"].get("skip_own_messages", True)),
        dry_run=bool(data["monitor"].get("dry_run", False)),
        state_file=str(data["monitor"].get("state_file", "storage/seen_messages.json")),
        require_link=bool(data["monitor"].get("require_link", True)),
    )

    if not monitor.source_groups:
        raise ConfigError("A lista 'source_groups' não pode estar vazia.")
    if not monitor.keywords:
        raise ConfigError("A lista 'keywords' não pode estar vazia.")

    return AppConfig(browser=browser, monitor=monitor)
