"""Bounded member turns through the gateway's session and approval services."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from gideon.core.config.loader import AppConfig
from gideon.engine.rooms.safety import (
    ProfileRefusal,
    RoomApprover,
    check_member_budget,
    member_spend_scope,
)
from gideon.engine.rooms.store import Room, RoomMember, RoomStore, session_key
from gideon.integrations.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
)
from gideon.security.guardrails.policy import profile_for_session
from gideon.security.security import redact_field


class RoomBusyError(RuntimeError):
    pass


def member_listens(member: RoomMember, content: str) -> bool:
    if member.listen_policy == "none":
        return False
    if member.listen_policy == "all":
        return True
    return any(
        re.search(
            r"(?<![\w@])@" + re.escape(label) + r"(?![\w-])", content, re.IGNORECASE
        )
        for label in {member.id, member.name}
    )


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
            f"{row.get('speaker', row['role'])}: {row['content']}"
            for row in messages[-100:]
        )
        role = (
            f" Your role in this room: {self.member.role}" if self.member.role else ""
        )
        return (
            f"You are {self.member.name}, a member of room {self.room.name}."
            + role
            + " Respond to the shared conversation as yourself.\n\n"
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

    async def run(self, room_id: str, content: str) -> list[dict]:
        config = AppConfig.load().rooms
        if not config.enabled:
            raise PermissionError("rooms are disabled")
        room = self.store.get(room_id)
        if not room.members:
            raise ValueError("room needs at least one member")
        if len(room.members) > config.max_members:
            raise ValueError("room exceeds the configured member limit")
        if room_id in self._active:
            raise RoomBusyError("room already has an active turn")
        self._active.add(room_id)
        try:
            self.store.append(room_id, "user", content, speaker="user")
            for turn in plan_member_turns(room, content, config.round_budget):
                member = turn.member
                if not AppConfig.load().rooms.enabled:
                    raise PermissionError("rooms are disabled")
                key = turn.key
                try:
                    posture = profile_for_session(key)
                    check_member_budget(key, posture)
                except ProfileRefusal as exc:
                    self.store.append(
                        room_id,
                        "system",
                        f"Profile refused for {member.id}: {exc}",
                        speaker="system",
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
                    try:
                        self.state.sessions.set_approval_policy(key, "ask")
                        approver = RoomApprover(self.state, identity="human")
                        chunks = []
                        async for event in provider.stream(
                            turn.prompt(self.store.messages(room_id))
                        ):
                            if not AppConfig.load().rooms.enabled:
                                await provider.cancel()
                                raise PermissionError("rooms are disabled")
                            if event.kind == EVENT_TEXT_CHUNK:
                                chunks.append(event.text)
                            elif event.kind == EVENT_COMPLETE:
                                spend.record(event)
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
                                room_id, "assistant", answer, speaker=member.id
                            )
                    except asyncio.CancelledError:
                        await provider.cancel()
                        raise
                    finally:
                        self.state.sessions.release(key)
            return self.store.messages(room_id)
        finally:
            self._active.discard(room_id)

    def active(self, room_id: str) -> bool:
        return room_id in self._active
