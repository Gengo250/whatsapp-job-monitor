from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class BrowserConfig:
    profile_dir: str
    headless: bool
    wait_timeout_seconds: int
    login_timeout_seconds: int


@dataclass(slots=True)
class MonitorConfig:
    archive_group: str
    source_groups: list[str]
    keywords: list[str]
    poll_interval_seconds: int
    lookback_messages: int
    skip_own_messages: bool
    dry_run: bool
    state_file: str
    require_link: bool = False


@dataclass(slots=True)
class AppConfig:
    browser: BrowserConfig
    monitor: MonitorConfig


@dataclass(slots=True)
class WhatsAppMessage:
    signature: str
    group_name: str
    sender: str
    timestamp_hint: str
    text: str
    is_from_me: bool
