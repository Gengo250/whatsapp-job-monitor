from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from time import sleep

from selenium import webdriver
from selenium.common.exceptions import (
    InvalidSessionIdException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver import ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.keyword_matcher import KeywordMatcher
from app.models import BrowserConfig, WhatsAppMessage

log = logging.getLogger("whatsapp.client")


class WhatsAppWebClient:
    SIDEBAR_SEARCH_XPATH = "//*[@id='side']//div[@contenteditable='true'][@role='textbox']"
    CHAT_RESULT_XPATH_TEMPLATE = (
        "//*[@id='pane-side']//span[@title={chat}]"
        "/ancestor::*[@role='gridcell' or @role='row'][1]"
        " | //*[@id='pane-side']//span[@title={chat}]"
    )
    CHAT_HEADER_TITLE_XPATH_TEMPLATE = (
        "//*[@id='main']//header//span[@title={chat}]"
        " | //*[@id='main']//header//*[normalize-space(text())={chat}]"
    )
    MESSAGE_CONTAINER_XPATH = "//*[@id='main']//div[@data-pre-plain-text]"
    SEARCH_RESULT_LISTITEM_XPATH = (
        "//*[@role='listitem'][.//*[contains(@class, 'matched-text')]]"
        "[not(ancestor::*[@id='side'])]"
    )
    CHAT_SEARCH_BUTTON_XPATH = (
        "//*[@id='main']//header//button[@aria-label='Search' or @data-tab='6']"
    )
    CHAT_SEARCH_INPUT_XPATH = (
        "//*[@id='main']//*[self::input or self::textarea or self::div]"
        "[((@role='textbox' and @contenteditable='true') or self::input or self::textarea)]"
        "[not(ancestor::footer)]"
        "[not(ancestor::*[@id='side'])]"
    )
    OLDER_MESSAGES_BUTTON_XPATH = "//button[.//div[contains(., 'older messages')]]"

    def __init__(self, root_dir: Path, config: BrowserConfig) -> None:
        self.root_dir = root_dir
        self.config = config
        self.driver: webdriver.Chrome | None = None
        self.wait: WebDriverWait | None = None

    def start(self) -> None:
        if self.driver is not None:
            return

        profile_dir = (self.root_dir / self.config.profile_dir).resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)

        options = ChromeOptions()
        options.add_argument(f"--user-data-dir={profile_dir}")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-notifications")
        options.add_argument("--lang=pt-BR")
        if self.config.headless:
            options.add_argument("--headless=new")

        self.driver = webdriver.Chrome(options=options)
        self.wait = WebDriverWait(self.driver, self.config.wait_timeout_seconds)

    def _ensure_live_session(self) -> None:
        if self.driver is None or self.wait is None:
            raise RuntimeError("Cliente do WhatsApp Web não foi iniciado.")

        try:
            _ = self.driver.current_url
        except (InvalidSessionIdException, WebDriverException):
            log.warning("Sessão do Chrome/ChromeDriver foi perdida. Reiniciando navegador.")
            self._restart_session()

    def _restart_session(self) -> None:
        try:
            if self.driver is not None:
                self.driver.quit()
        except Exception:
            pass

        self.driver = None
        self.wait = None
        self.start()
        self.open()
        self.ensure_logged_in()

    def _refetch_search_result_item(self, index: int) -> WebElement | None:
        self._require_driver()
        items = self.driver.find_elements(By.XPATH, self.SEARCH_RESULT_LISTITEM_XPATH)
        if index >= len(items):
            return None
        return items[index]

    def _refetch_visible_message_element(self, index: int) -> WebElement | None:
        self._require_driver()
        elements = self.driver.find_elements(By.XPATH, self.MESSAGE_CONTAINER_XPATH)
        if index >= len(elements):
            return None
        return elements[index]

    def open(self) -> None:
        self._require_driver()
        self._ensure_live_session()
        self.driver.get("https://web.whatsapp.com/")

    def ensure_logged_in(self) -> None:
        self._require_wait()
        self._ensure_live_session()

        try:
            self.wait.until(EC.presence_of_element_located((By.ID, "pane-side")))
            return
        except TimeoutException:
            log.info("WhatsApp Web não parece pronto ainda. Escaneie o QR Code, se necessário.")

        long_wait = WebDriverWait(self.driver, self.config.login_timeout_seconds)
        long_wait.until(EC.presence_of_element_located((By.ID, "pane-side")))
        log.info("Login detectado com sucesso.")

    def open_chat(self, chat_name: str) -> None:
        self._require_wait()
        self._ensure_live_session()
        self.ensure_logged_in()

        for attempt in range(1, 4):
            try:
                search_box = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, self.SIDEBAR_SEARCH_XPATH))
                )
                self._clear_box(search_box)
                search_box.send_keys(chat_name)

                result = self.wait.until(
                    EC.element_to_be_clickable(
                        (
                            By.XPATH,
                            self.CHAT_RESULT_XPATH_TEMPLATE.format(chat=self._xpath_literal(chat_name)),
                        )
                    )
                )
                self._safe_click(result)

                self.wait.until(
                    EC.presence_of_element_located(
                        (
                            By.XPATH,
                            self.CHAT_HEADER_TITLE_XPATH_TEMPLATE.format(chat=self._xpath_literal(chat_name)),
                        )
                    )
                )
                self._clear_box(search_box)
                sleep(0.5)
                return
            except Exception as exc:
                log.warning("Tentativa %s de abrir '%s' falhou: %s", attempt, chat_name, exc)
                if attempt == 3:
                    raise
                sleep(1.0)

    def search_recent_messages_in_chat(
        self,
        group_name: str,
        keywords: list[str],
        limit: int = 30,
        require_link: bool = False,
    ) -> list[WhatsAppMessage]:
        self.open_chat(group_name)
        self._click_older_messages_button_if_present()

        if keywords:
            try:
                self._search_inside_current_chat(keywords[0])
                matched = self._collect_messages_from_search_results(
                    chat_name=group_name,
                    keywords=keywords,
                    limit=limit,
                    require_link=require_link,
                )
                if matched:
                    return matched
            except Exception as exc:
                log.warning(
                    "Busca interna no chat '%s' falhou, seguindo com leitura visível: %s",
                    group_name,
                    exc,
                )

        sleep(1.0)
        return self._collect_visible_messages(group_name, limit=limit)

    def read_recent_messages(self, chat_name: str, limit: int = 30) -> list[WhatsAppMessage]:
        self.open_chat(chat_name)
        return self._collect_visible_messages(chat_name, limit=limit)

    def send_message(self, chat_name: str, text: str) -> None:
        self.open_chat(chat_name)
        self._require_wait()
        self._ensure_live_session()

        composer = self.wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//*[@id='main']//footer//*[@contenteditable='true'][@role='textbox']",
                )
            )
        )
        composer.click()
        sleep(0.2)

        clean_text = self._sanitize_text_for_send_keys(text)
        lines = clean_text.splitlines() or [clean_text]

        for index, line in enumerate(lines):
            self._send_line_to_composer(composer, line)
            if index < len(lines) - 1:
                composer.send_keys(Keys.SHIFT, Keys.ENTER)

        composer.send_keys(Keys.ENTER)
        sleep(0.8)

    @staticmethod
    def _sanitize_text_for_send_keys(text: str) -> str:
        sanitized_chars: list[str] = []
        replaced = 0

        for char in text:
            codepoint = ord(char)
            if char == "\x00":
                replaced += 1
                continue
            if codepoint > 0xFFFF:
                sanitized_chars.append("□")
                replaced += 1
                continue
            sanitized_chars.append(char)

        sanitized = "".join(sanitized_chars)
        if replaced:
            log.info(
                "Mensagem continha %s caractere(s) fora do BMP ou nulos; substituindo antes do send_keys.",
                replaced,
            )
        return sanitized

    def _send_line_to_composer(self, composer: WebElement, line: str) -> None:
        try:
            composer.send_keys(line)
        except WebDriverException as exc:
            message = str(exc)
            if "ChromeDriver only supports characters in the BMP" not in message:
                raise

            fallback = self._sanitize_text_for_send_keys(line)
            if fallback != line:
                composer.send_keys(fallback)
                return
            raise

    def close(self) -> None:
        if self.driver is not None:
            self.driver.quit()
            self.driver = None
            self.wait = None

    def _search_inside_current_chat(self, keyword: str) -> None:
        self._require_wait()
        self._require_driver()

        button = self.wait.until(
            EC.element_to_be_clickable((By.XPATH, self.CHAT_SEARCH_BUTTON_XPATH))
        )
        self._safe_click(button)
        sleep(0.4)

        input_box = self._resolve_in_chat_search_input()
        self._clear_box(input_box)
        input_box.send_keys(keyword)
        sleep(1.0)

    def _resolve_in_chat_search_input(self) -> WebElement:
        self._require_wait()
        self._require_driver()

        active = self.driver.switch_to.active_element
        if self._is_candidate_search_input(active):
            return active

        candidates = self.wait.until(
            lambda drv: [
                element
                for element in drv.find_elements(By.XPATH, self.CHAT_SEARCH_INPUT_XPATH)
                if self._is_candidate_search_input(element)
            ]
        )
        if not candidates:
            raise TimeoutException("Campo de busca interna do chat não encontrado.")
        return candidates[0]

    def _is_candidate_search_input(self, element: WebElement | None) -> bool:
        if element is None:
            return False
        if not element.is_displayed():
            return False

        tag = (element.tag_name or "").lower()
        role = (element.get_attribute("role") or "").lower()
        contenteditable = (element.get_attribute("contenteditable") or "").lower()

        if tag in {"input", "textarea"}:
            return True
        return role == "textbox" and contenteditable == "true"

    def _collect_messages_from_search_results(
        self,
        chat_name: str,
        keywords: list[str],
        limit: int = 30,
        require_link: bool = False,
    ) -> list[WhatsAppMessage]:
        self._require_driver()
        matcher = KeywordMatcher(keywords)
        sleep(1.0)

        initial_items = self.driver.find_elements(By.XPATH, self.SEARCH_RESULT_LISTITEM_XPATH)
        max_items = min(limit, len(initial_items))
        messages: list[WhatsAppMessage] = []

        for index in range(max_items):
            for attempt in range(1, 4):
                item = self._refetch_search_result_item(index)
                if item is None:
                    break

                try:
                    text = self._extract_search_result_text(item)
                    if not text:
                        break

                    if not matcher.matches(text, require_link=require_link):
                        break

                    sender, preview = self._parse_search_result_sender_and_preview(item)
                    timestamp_hint = self._extract_search_result_timestamp(item)
                    signature = self._build_signature(chat_name, f"{sender}|{timestamp_hint}", text)

                    try:
                        self._safe_click(item)
                        sleep(0.3)
                    except StaleElementReferenceException:
                        if attempt == 3:
                            log.debug(
                                "Resultado %s da busca interna ficou stale após 3 tentativas no chat '%s'.",
                                index,
                                chat_name,
                            )
                        else:
                            sleep(0.25)
                            continue
                    except Exception:
                        pass

                    messages.append(
                        WhatsAppMessage(
                            signature=signature,
                            group_name=chat_name,
                            sender=sender,
                            timestamp_hint=timestamp_hint,
                            text=text or preview,
                            is_from_me=False,
                        )
                    )
                    break

                except StaleElementReferenceException:
                    if attempt == 3:
                        log.debug(
                            "Resultado %s da busca interna ficou stale após 3 tentativas no chat '%s'.",
                            index,
                            chat_name,
                        )
                    else:
                        sleep(0.25)
                        continue

        return messages

    def _collect_visible_messages(self, chat_name: str, limit: int = 30) -> list[WhatsAppMessage]:
        self._require_driver()

        elements = self.driver.find_elements(By.XPATH, self.MESSAGE_CONTAINER_XPATH)
        start_index = max(0, len(elements) - limit)
        target_indexes = list(range(start_index, len(elements)))

        messages: list[WhatsAppMessage] = []

        for dom_index in target_indexes:
            for attempt in range(1, 4):
                element = self._refetch_visible_message_element(dom_index)
                if element is None:
                    break

                try:
                    text = self._extract_message_text(element)
                    if not text:
                        break

                    meta = element.get_attribute("data-pre-plain-text") or ""
                    sender, timestamp_hint = self._parse_metadata(meta)
                    is_from_me = self._is_outgoing(element)
                    signature = self._build_signature(chat_name, meta, text)

                    messages.append(
                        WhatsAppMessage(
                            signature=signature,
                            group_name=chat_name,
                            sender=sender,
                            timestamp_hint=timestamp_hint,
                            text=text,
                            is_from_me=is_from_me,
                        )
                    )
                    break

                except StaleElementReferenceException:
                    if attempt == 3:
                        log.debug(
                            "Mensagem visível índice %s ficou stale após 3 tentativas no chat '%s'.",
                            dom_index,
                            chat_name,
                        )
                    else:
                        sleep(0.20)
                        continue

        return messages

    def _extract_search_result_text(self, element: WebElement) -> str:
        title_candidates = []
        for node in element.find_elements(By.XPATH, ".//*[@title]"):
            value = (node.get_attribute("title") or "").strip()
            if value:
                title_candidates.append(value)

        if title_candidates:
            return max(title_candidates, key=len).strip()

        return (element.text or "").strip()

    def _parse_search_result_sender_and_preview(self, element: WebElement) -> tuple[str, str]:
        raw = (element.text or "").strip()
        if ":" in raw:
            sender, preview = raw.split(":", 1)
            return sender.strip(), preview.strip()
        return "", raw

    def _extract_search_result_timestamp(self, element: WebElement) -> str:
        nodes = element.find_elements(By.XPATH, ".//*[@aria-colindex='2']//span[normalize-space()]")
        for node in nodes:
            value = (node.text or "").strip()
            if value:
                return value
        return ""

    def _click_older_messages_button_if_present(self) -> None:
        self._require_driver()
        buttons = self.driver.find_elements(By.XPATH, self.OLDER_MESSAGES_BUTTON_XPATH)
        if buttons:
            try:
                self._safe_click(buttons[0])
                sleep(1.2)
            except Exception:
                pass

    def _extract_message_text(self, element: WebElement) -> str:
        text_nodes = element.find_elements(By.XPATH, ".//span[contains(@class, 'selectable-text')]")
        parts = [node.text.strip() for node in text_nodes if node.text.strip()]
        if parts:
            return "\n".join(parts)
        return (element.text or "").strip()

    def _is_outgoing(self, element: WebElement) -> bool:
        outgoing = element.find_elements(By.XPATH, "ancestor::*[contains(@class, 'message-out')][1]")
        return bool(outgoing)

    def _safe_click(self, element: WebElement) -> None:
        self._require_driver()
        try:
            element.click()
        except StaleElementReferenceException:
            raise
        except Exception:
            self.driver.execute_script("arguments[0].click();", element)

    def _clear_box(self, element: WebElement) -> None:
        element.click()
        sleep(0.2)
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.BACKSPACE)
        sleep(0.2)

    @staticmethod
    def _parse_metadata(raw: str) -> tuple[str, str]:
        clean = raw.strip()
        match = re.match(r"^\[(.*?)\]\s*(.*?):\s*$", clean)
        if match:
            return match.group(2).strip(), match.group(1).strip()

        match = re.match(r"^\[(.*?)\]\s*$", clean)
        if match:
            return "", match.group(1).strip()

        return "", clean

    @staticmethod
    def _build_signature(chat_name: str, metadata: str, text: str) -> str:
        raw = f"{chat_name}|{metadata}|{text}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _xpath_literal(value: str) -> str:
        if "'" not in value:
            return f"'{value}'"
        if '"' not in value:
            return f'"{value}"'
        parts = value.split("'")
        return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"

    def _require_driver(self) -> None:
        if self.driver is None:
            raise RuntimeError("Driver do Selenium não foi iniciado.")

    def _require_wait(self) -> None:
        if self.wait is None or self.driver is None:
            raise RuntimeError("Cliente do WhatsApp Web não foi iniciado.")
