"""Bounded member turns through the gateway's session and approval services."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
import unicodedata
from dataclasses import dataclass
from uuid import uuid4

from gideon.core.config.loader import AppConfig
from gideon.engine.rooms.safety import (
    ProfileRefusal,
    RoomApprover,
    check_member_budget,
    member_spend_scope,
)
from gideon.engine.rooms.store import (
    Room,
    RoomBusyError,
    RoomMember,
    RoomStore,
    message_text,
    session_key,
)
from gideon.integrations.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
)
from gideon.security.guardrails.policy import profile_for_session
from gideon.security.security import redact_field


def member_listens(member: RoomMember, content: str) -> bool:
    if member.listen_policy == "none":
        return False
    if member.listen_policy == "all":
        return True
    normalized = unicodedata.normalize("NFC", content).casefold()

    def token_character(char: str) -> bool:
        return char in "_@-" or unicodedata.category(char)[0] in "LNM"

    for label in {"everyone", member.id, member.name}:
        token = "@" + unicodedata.normalize("NFC", label).casefold()
        for match in re.finditer(re.escape(token), normalized):
            if match.start() and token_character(normalized[match.start() - 1]):
                continue
            if match.end() < len(normalized) and token_character(
                normalized[match.end()]
            ):
                continue
            return True
    return False


@dataclass(frozen=True)
class MemberTurn:
    room: Room
    member: RoomMember

    @property
    def key(self) -> str:
        return session_key(self.room.id, self.member.id)

    @property
    def provider_options(self) -> dict:
        return {
            "agent": self.member.agent,
            "approval_policy": "ask",
            "unattended": False,
        }

    def prompt(self, messages: list[dict]) -> str:
        context = "\n".join(
            f"{row.get('speaker_name', row.get('speaker', row['role']))}: {row['content']}"
            for row in messages[-100:]
        )
        role = (
            f" Your role in this room: {self.member.role}" if self.member.role else ""
        )
        return (
            f"You are {self.member.name}, a member of room {self.room.name}."
            + role
            + " Respond to the shared conversation as yourself. Read teammates' previous replies; "
            "do not impersonate them or ask the user to repeat shared context.\n"
            + "Room members: "
            + "; ".join(
                f"{member.name} (@{member.id})"
                + (f": {member.role}" if member.role else "")
                for member in self.room.members
            )
            + "\n\n"
            + context
        )


def plan_member_turns(room: Room, content: str, round_budget: int) -> list[MemberTurn]:
    return [
        MemberTurn(room, member)
        for member in room.members
        if member_listens(member, content)
    ][:round_budget]


class RoomTurns:
    def __init__(self, store: RoomStore, state):
        self.store = store
        self.state = state
        self._active: set[str] = set()
        self._bindings: dict[str, str] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self.store.recover_interrupted()

    def submit(self, room_id: str, content: str, request_id: str) -> dict:
        content = message_text(content)
        if (
            not isinstance(request_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request_id)
            or request_id == "current"
        ):
            raise ValueError(
                "request_id must contain 1 to 80 letters, numbers, underscores or hyphens"
            )
        config = AppConfig.load().rooms
        if not config.enabled:
            raise PermissionError("rooms are disabled")
        room = self.store.get(room_id)
        if not room.members:
            raise ValueError("room needs at least one member")
        if len(room.members) > config.max_members:
            raise ValueError("room exceeds the configured member limit")
        existing = self.store.turn(room_id, request_id)
        if existing:
            if (
                existing.get("content_hash")
                != hashlib.sha256(content.encode()).hexdigest()
            ):
                raise RoomBusyError("request id was already used for different text")
            return existing
        lock = self.store.acquire_turn(room_id)
        try:
            record, created = self.store.begin_turn(room_id, content, request_id)
            if not created:
                lock.close()
                return record
        except BaseException:
            lock.close()
            raise
        self._active.add(room_id)
        task = asyncio.create_task(
            self._execute(room, content, record["id"], lock),
            name=f"room:{room_id}:{record['id']}",
        )
        self._tasks[room_id] = task

        def finished(done: asyncio.Task) -> None:
            if self._tasks.get(room_id) is done:
                self._tasks.pop(room_id, None)
                self._active.discard(room_id)
            lock.close()
            if done.cancelled():
                self.store.update_turn(room_id, record["id"], status="cancelled")
            else:
                done.exception()

        task.add_done_callback(finished)
        return record

    async def run(self, room_id: str, content: str) -> list[dict]:
        self.submit(room_id, content, uuid4().hex)
        await asyncio.shield(self._tasks[room_id])
        return self.store.messages(room_id)

    async def _execute(self, room: Room, content: str, turn_id: str, lock) -> None:
        room_id = room.id
        self.store.update_turn(room_id, turn_id, status="running")
        try:
            async with asyncio.timeout(900):
                await self._members(room, content, turn_id)
            self.store.update_turn(
                room_id, turn_id, status="completed", member_id=None, text=""
            )
        except asyncio.CancelledError:
            self.store.update_turn(room_id, turn_id, status="cancelled")
            raise
        except Exception as exc:
            error = (
                "Room turn exceeded the 15 minute limit"
                if isinstance(exc, TimeoutError)
                else str(exc)
            )
            self.store.update_turn(
                room_id, turn_id, status="failed", error=redact_field(error)[:1000]
            )
            raise
        finally:
            self._active.discard(room_id)
            lock.close()

    async def _members(self, room: Room, content: str, turn_id: str) -> None:
        room_id = room.id
        config = AppConfig.load().rooms
        for turn in plan_member_turns(room, content, config.round_budget):
            member = turn.member
            if not AppConfig.load().rooms.enabled:
                raise PermissionError("rooms are disabled")
            key = turn.key
            self.store.update_turn(room_id, turn_id, member_id=member.id, text="")
            try:
                posture = profile_for_session(key)
                check_member_budget(key, posture)
            except ProfileRefusal as exc:
                self.store.append(
                    room_id,
                    "system",
                    f"Profile refused for {member.id}: {exc}",
                    speaker="system",
                    turn_id=turn_id,
                )
                continue
            previous = self._bindings.get(key) or self.state.sessions.get_agent(key)
            if previous and previous != member.agent:
                await self.state.sessions.destroy(key)
            with member_spend_scope(key, posture) as spend:
                provider, _, _ = await self.state.sessions.get_or_create(
                    key,
                    **turn.provider_options,
                )
                self._bindings[key] = member.agent
                chunks = []
                saved = False
                last_update = 0.0
                size = 0
                try:
                    self.state.sessions.set_approval_policy(key, "ask")
                    approver = RoomApprover(self.state, identity="human")
                    async for event in provider.stream(
                        turn.prompt(self.store.messages(room_id, limit=100))
                    ):
                        if not AppConfig.load().rooms.enabled:
                            await provider.cancel()
                            raise PermissionError("rooms are disabled")
                        if event.kind == EVENT_TEXT_CHUNK:
                            size += len(event.text)
                            if size > 100000:
                                await provider.cancel()
                                raise ValueError(
                                    "Member reply exceeded the 100000 character limit"
                                )
                            chunks.append(event.text)
                            if time.monotonic() - last_update >= 0.25:
                                self.store.update_turn(
                                    room_id, turn_id, text=redact_field("".join(chunks))
                                )
                                last_update = time.monotonic()
                        elif event.kind == EVENT_COMPLETE:
                            spend.record(event)
                            if event.stop_reason in {"error", "failed"}:
                                raise RuntimeError(
                                    event.text or "Member could not complete the turn"
                                )
                        elif event.kind == "error":
                            raise RuntimeError(
                                event.text
                                or event.title
                                or "Member could not complete the turn"
                            )
                        elif event.kind == EVENT_PERMISSION_REQUEST:
                            current_posture = profile_for_session(key)
                            approved = await approver.approve(
                                key, event, current_posture
                            )
                            if approved and AppConfig.load().rooms.enabled:
                                await provider.approve_tool(event.request_id)
                            else:
                                await provider.reject_tool(event.request_id)
                    answer = redact_field("".join(chunks))
                    if answer.strip():
                        self.store.append(
                            room_id,
                            "assistant",
                            answer,
                            speaker=member.id,
                            turn_id=turn_id,
                        )
                    saved = True
                except asyncio.CancelledError:
                    await provider.cancel()
                    raise
                finally:
                    if not saved and chunks:
                        answer = redact_field("".join(chunks))
                        self.store.update_turn(room_id, turn_id, text=answer)
                        if answer.strip():
                            self.store.append(
                                room_id,
                                "assistant",
                                answer,
                                speaker=member.id,
                                turn_id=turn_id,
                                interrupted=True,
                            )
                    self.state.sessions.release(key)

    async def cancel(self, room_id: str) -> dict | None:
        self.store.get(room_id)
        task = self._tasks.get(room_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return self.store.turn(room_id)

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def active(self, room_id: str) -> bool:
        current = self.store.turn(room_id)
        return room_id in self._active or bool(
            current and current["status"] in {"queued", "running"}
        )
