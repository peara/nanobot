from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx
from github import Github

from nanobot.channels.base import Channel, IncomingMessage

logger = logging.getLogger(__name__)


class GithubChannel(Channel):
    def __init__(
        self,
        token: str,
        bot_username: str,
        repo_owner: str,
        repo_name: str,
        poll_interval: int = 30,
        trigger: str = "assignment",
        label_name: str = "nanobot",
        opencode_url: str = "http://localhost:4096",
        opencode_username: str = "opencode",
        opencode_password: str | None = None,
        notification_chat_id: int | None = None,
        telegram_channel: Any = None,
    ) -> None:
        super().__init__()
        self.token = token
        self.bot_username = bot_username
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.poll_interval = poll_interval
        self.trigger = trigger
        self.label_name = label_name
        self.opencode_url = opencode_url
        self.opencode_username = opencode_username
        self.opencode_password = opencode_password
        self.notification_chat_id = notification_chat_id
        self.telegram_channel = telegram_channel

        self._github: Any = None
        self._stop_event = asyncio.Event()
        self._poll_task: asyncio.Task | None = None

        self._active_issue: Any | None = None
        self._active_session_id: str | None = None
        self._last_comment_id: int | None = None

    async def start(self) -> None:
        self._github = Github(self.token)  # type: ignore

        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(
            "GitHub channel started for %s/%s, polling every %ds",
            self.repo_owner,
            self.repo_name,
            self.poll_interval,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

        if self._github:
            self._github.close()

        logger.info("GitHub channel stopped")

    async def send(self, chat_id: str, text: str) -> None:
        if self._github is None:
            raise RuntimeError("GitHub channel not started")

        if not chat_id.startswith("github:"):
            raise ValueError(f"Invalid github chat_id format: {chat_id}")

        try:
            parts = chat_id.replace("github:", "").split("#")
            issue_num = int(parts[1])

            repo = self._github.get_repo(f"{self.repo_owner}/{self.repo_name}")
            issue = repo.get_issue(issue_num)
            issue.create_comment(text)
            logger.info("Posted comment to issue #%d", issue_num)
        except Exception:
            logger.exception("Failed to post comment to %s", chat_id)

    async def _send_telegram_notification(self, text: str) -> None:
        if self.notification_chat_id is None or self.telegram_channel is None:
            logger.debug("Telegram notifications not configured, skipping notification")
            return

        try:
            await self.telegram_channel.send(str(self.notification_chat_id), text)
            logger.info("Sent Telegram notification to chat_id=%s", self.notification_chat_id)
        except Exception:
            logger.exception("Failed to send Telegram notification (continuing workflow)")

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._check_for_work()
            except Exception:
                logger.exception("Error in GitHub poll loop")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.poll_interval,
                )
            except asyncio.TimeoutError:
                continue

    async def _check_for_work(self) -> None:
        if self._github is None:
            return

        try:
            repo = self._github.get_repo(f"{self.repo_owner}/{self.repo_name}")

            if self._active_issue is not None:
                await self._process_active_issue(repo)
                return

            await self._find_new_work(repo)

        except Exception:
            logger.exception("Error checking for work")

    async def _find_new_work(self, repo: Any) -> None:
        issues = repo.get_issues(assignee=self.bot_username, state="open")

        for issue in issues:
            if self._should_process_issue(issue):
                await self._start_work(issue)
                return

    def _should_process_issue(self, issue: Any) -> bool:
        if self.trigger == "assignment":
            for assignee in issue.assignees:
                if assignee.login == self.bot_username:
                    return True
            return False

        elif self.trigger == "label":
            for label in issue.labels:
                if label.name == self.label_name:
                    return True
            return False

        return False

    async def _start_work(self, issue: Any) -> None:
        self._active_issue = issue
        logger.info("Starting work on issue #%d: %s", issue.number, issue.title)

        session_id = await self._create_opencode_session(issue.title)
        if session_id is None:
            logger.error("Failed to create Opencode session for issue #%d", issue.number)
            return

        self._active_session_id = session_id

        notification_msg = (
            f'🔧 Started: {self.repo_owner}/{self.repo_name}#{issue.number} - "{issue.title}"\nSession: {session_id}'
        )
        await self._send_telegram_notification(notification_msg)

        await asyncio.sleep(1)

        description = issue.body or "No description provided."
        initial_task = (
            f"Please analyze and work on this issue:\n\n**Title:** {issue.title}\n\n**Description:**\n{description}"
        )
        logger.info("Sending initial task to session %s for issue #%d", session_id, issue.number)
        response = await self._call_opencode(session_id, initial_task)

        intro = (
            f"Starting work on this issue. Created Opencode session `{session_id}`.\n\n"
            f"Initial task sent. Response:\n{response or '(no response yet)'[:500]}"
        )
        issue.create_comment(intro)

    async def _process_active_issue(self, repo: Any) -> None:
        if self._active_issue is None or self._active_session_id is None:
            return

        issue = repo.get_issue(self._active_issue.number)
        self._active_issue = issue

        comments = issue.get_comments(since=self._get_last_check_time())

        for comment in comments:
            if comment.id == self._last_comment_id:
                continue

            if comment.user.login == self.bot_username:
                continue

            await self._process_user_comment(issue, comment)
            self._last_comment_id = comment.id

    async def _process_user_comment(self, issue: Any, comment: Any) -> None:
        text = comment.body.strip()

        if not text:
            return

        logger.info("Processing comment from %s on issue #%d", comment.user.login, issue.number)

        response = await self._call_opencode(self._active_session_id, text)
        if response:
            reply = f"{response}\n\n--SessionID:{self._active_session_id}--"
            issue.create_comment(reply)

            comment_preview = response[:200] if len(response) > 200 else response
            await self._send_telegram_notification(
                f"💬 New comment on {self.repo_owner}/{self.repo_name}#{issue.number}:\n{comment_preview}"
            )

    async def _create_opencode_session(self, title: str) -> str | None:
        try:
            async with httpx.AsyncClient() as client:
                if self.opencode_password:
                    response = await client.post(
                        f"{self.opencode_url}/session",
                        json={"title": title},
                        auth=httpx.BasicAuth(self.opencode_username, self.opencode_password),
                        timeout=30.0,
                    )
                else:
                    response = await client.post(
                        f"{self.opencode_url}/session",
                        json={"title": title},
                        timeout=30.0,
                    )
                response.raise_for_status()
                data = response.json()
                session_id = data.get("id")
                logger.info("Created Opencode session: %s", session_id)
                return session_id
        except Exception:
            logger.exception("Failed to create Opencode session")
            return None

    async def _call_opencode(self, session_id: str | None, message: str) -> str | None:
        if session_id is None:
            return None

        try:
            async with httpx.AsyncClient() as client:
                payload = {"parts": [{"type": "text", "text": message}]}
                if self.opencode_password:
                    response = await client.post(
                        f"{self.opencode_url}/session/{session_id}/message",
                        json=payload,
                        auth=httpx.BasicAuth(self.opencode_username, self.opencode_password),
                        timeout=120.0,
                    )
                else:
                    response = await client.post(
                        f"{self.opencode_url}/session/{session_id}/message",
                        json=payload,
                        timeout=120.0,
                    )
                response.raise_for_status()
                data = response.json()

                parts = data.get("parts", [])
                text_parts = [p.get("text", "") for p in parts if p.get("type") == "text"]
                return "\n".join(text_parts) if text_parts else None

        except httpx.HTTPStatusError as exc:
            logger.exception("Failed to call Opencode: %s", exc.response.text)
            return "Sorry, I encountered an error processing your request."
        except Exception:
            logger.exception("Failed to call Opencode")
            return "Sorry, I encountered an error processing your request."

    async def _notify_handler(self, issue: Any, text: str) -> None:
        if self._handler is None:
            return

        message = IncomingMessage(
            channel="github",
            chat_id=f"github:{self.repo_owner}/{self.repo_name}#{issue.number}",
            user_id=str(issue.user.login if issue.user else "unknown"),
            text=text,
        )
        await self._handler(message)

    def _get_last_check_time(self) -> datetime:
        return datetime.now() - self._get_check_window()

    def _get_check_window(self) -> timedelta:
        return timedelta(seconds=self.poll_interval + 5)
