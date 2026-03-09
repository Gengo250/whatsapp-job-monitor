from __future__ import annotations

from pathlib import Path

from app.config import load_config
from app.logging_setup import configure_logging
from app.monitor_service import MonitorService
from app.whatsapp_client import WhatsAppWebClient


def main() -> None:
    root_dir = Path(__file__).resolve().parent
    configure_logging()

    config = load_config(root_dir / "config.json")
    client = WhatsAppWebClient(root_dir=root_dir, config=config.browser)
    service = MonitorService(root_dir=root_dir, config=config.monitor, client=client)
    service.run_forever()


if __name__ == "__main__":
    main()
