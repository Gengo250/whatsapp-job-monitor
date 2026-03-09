from __future__ import annotations

import logging
import time
from pathlib import Path

from app.formatter import ForwardMessageFormatter
from app.keyword_matcher import KeywordMatcher
from app.models import MonitorConfig, WhatsAppMessage
from app.state_store import SeenMessageStore
from app.whatsapp_client import WhatsAppWebClient

log = logging.getLogger("monitor.service")


class MonitorService:
    def __init__(self, root_dir: Path, config: MonitorConfig, client: WhatsAppWebClient) -> None:
        self.root_dir = root_dir
        self.config = config
        self.client = client
        self.matcher = KeywordMatcher(config.keywords)
        self.formatter = ForwardMessageFormatter()
        self.state = SeenMessageStore((root_dir / config.state_file).resolve())

    def run_forever(self) -> None:
        self.client.start()
        self.client.open()
        self.client.ensure_logged_in()

        log.info("Monitor iniciado.")
        log.info("Grupo de arquivo: %s", self.config.archive_group)
        log.info("Grupos monitorados: %s", ", ".join(self.config.source_groups))
        log.info("Palavras-chave: %s", ", ".join(self.config.keywords))
        log.info("Exigir link na mensagem: %s", self.config.require_link)
        log.info("Modo de teste (dry_run): %s", self.config.dry_run)

        try:
            while True:
                forwarded_count = 0
                for group_name in self.config.source_groups:
                    forwarded_count += self._process_group(group_name)

                self.state.persist()
                log.info(
                    "Ciclo concluído | encaminhadas=%s | próxima varredura em %ss",
                    forwarded_count,
                    self.config.poll_interval_seconds,
                )
                time.sleep(self.config.poll_interval_seconds)
        finally:
            self.state.persist()
            self.client.close()

    def _process_group(self, group_name: str) -> int:
        try:
            messages = self.client.search_recent_messages_in_chat(
                group_name=group_name,
                keywords=self.config.keywords,
                limit=self.config.lookback_messages,
                require_link=self.config.require_link,
            )
        except Exception as exc:
            log.exception("Falha ao ler o grupo '%s': %s", group_name, exc)
            return 0

        forwarded = 0
        for message in messages:
            if self._should_skip(message):
                continue

            if self.config.dry_run:
                log.info("[DRY RUN] Correspondência encontrada em '%s': %s", group_name, message.text)
                self.state.add(message.signature)
                forwarded += 1
                continue

            payload = self.formatter.format(message)
            try:
                self.client.send_message(self.config.archive_group, payload)
                self.state.add(message.signature)
                forwarded += 1
                log.info("Mensagem encaminhada de '%s' para '%s'.", group_name, self.config.archive_group)
                time.sleep(1.0)
            except Exception as exc:
                log.exception(
                    "Falha ao encaminhar mensagem do grupo '%s' para '%s': %s",
                    group_name,
                    self.config.archive_group,
                    exc,
                )

        return forwarded

    def _should_skip(self, message: WhatsAppMessage) -> bool:
        if self.state.has(message.signature):
            return True
        if self.config.skip_own_messages and message.is_from_me:
            return True
        if not message.text.strip():
            return True
        if not self.matcher.matches(message.text, require_link=self.config.require_link):
            return True
        return False
