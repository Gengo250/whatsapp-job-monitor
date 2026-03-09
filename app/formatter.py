from __future__ import annotations

from app.models import WhatsAppMessage


class ForwardMessageFormatter:
    def format(self, message: WhatsAppMessage) -> str:
        sender = message.sender or "Autor não identificado"
        timestamp = message.timestamp_hint or "Horário não identificado"

        return (
            "[Monitor de Estágio]\n"
            f"Grupo origem: {message.group_name}\n"
            f"Autor: {sender}\n"
            f"Quando: {timestamp}\n"
            "Mensagem:\n"
            f"{message.text}"
        )
