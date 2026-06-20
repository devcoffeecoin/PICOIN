from __future__ import annotations

import json
from pathlib import Path

from picoin_forge_l2.common.hashing import hash_json, sha256_text
from picoin_forge_l2.common.models import (
    AIChatMessage,
    AIChatMessageCreateRequest,
    AIChatSession,
    AIChatSessionCreateRequest,
    AIInferenceCreateRequest,
    AIRequestStatus,
    utc_now,
)

from .ai_access_queue import AIAccessQueue
from .storage import CoordinatorStorage


class AIChatManager:
    """Session memory for the Picoin Forge AI access queue."""

    def __init__(self, state_dir: str | Path, queue: AIAccessQueue):
        self.state_dir = Path(state_dir)
        self.storage = CoordinatorStorage(self.state_dir)
        self.queue = queue

    def create_session(self, request: AIChatSessionCreateRequest) -> AIChatSession:
        now = utc_now()
        requester_wallet = request.requester_wallet.strip().upper()
        session = AIChatSession(
            session_id="ai_chat_" + hash_json(
                {
                    "requester_wallet": requester_wallet,
                    "title": request.title,
                    "created_at": now.isoformat(),
                }
            )[:18],
            requester_wallet=requester_wallet,
            stake_snapshot_pi=request.stake_snapshot_pi,
            title=(request.title or "").strip() or None,
            required_capabilities=normalize_capabilities(request.required_capabilities),
            model_hint=request.model_hint,
            min_parameter_count_b=request.min_parameter_count_b,
            min_context_tokens=request.min_context_tokens,
            preferred_provider=request.preferred_provider,
            max_tokens=request.max_tokens,
            store_output=request.store_output,
            created_at=now,
            updated_at=now,
        )
        self.put_session(session)
        self.storage.record_event(
            "ai_chat.session_created",
            session.session_id,
            {
                "requester_wallet": session.requester_wallet,
                "required_capabilities": session.required_capabilities,
                "model_hint": session.model_hint,
                "preferred_provider": session.preferred_provider,
                "no_per_task_payment": True,
            },
        )
        return session

    def get_session(self, session_id: str) -> AIChatSession:
        with self.storage.connect() as connection:
            row = connection.execute("SELECT payload FROM ai_chat_sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"AI chat session not found: {session_id}")
        return AIChatSession.model_validate(json.loads(row["payload"]))

    def list_sessions(self, *, requester_wallet: str | None = None, limit: int = 100) -> list[AIChatSession]:
        safe_limit = max(1, min(int(limit), 1000))
        params: list[object] = []
        query = "SELECT payload FROM ai_chat_sessions"
        if requester_wallet:
            query += " WHERE requester_wallet = ?"
            params.append(requester_wallet.strip().upper())
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(safe_limit)
        with self.storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [AIChatSession.model_validate(json.loads(row["payload"])) for row in rows]

    def put_session(self, session: AIChatSession) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_chat_sessions (
                    session_id,
                    requester_wallet,
                    payload,
                    updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    requester_wallet = excluded.requester_wallet,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    session.session_id,
                    session.requester_wallet,
                    session.model_dump_json(),
                    session.updated_at.isoformat(),
                ),
            )

    def list_messages(self, session_id: str) -> list[AIChatMessage]:
        self.get_session(session_id)
        with self.storage.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload
                FROM ai_chat_messages
                WHERE session_id = ?
                ORDER BY created_at ASC, message_id ASC
                """,
                (session_id,),
            ).fetchall()
        return [AIChatMessage.model_validate(json.loads(row["payload"])) for row in rows]

    def send_message(self, session_id: str, request: AIChatMessageCreateRequest) -> dict:
        session = self.get_session(session_id)
        messages = self.list_messages(session_id)
        ai_prompt = build_chat_prompt(session, messages, request.prompt)
        ai_request = self.queue.create(
            AIInferenceCreateRequest(
                requester_wallet=session.requester_wallet,
                stake_snapshot_pi=session.stake_snapshot_pi,
                prompt=ai_prompt,
                required_capabilities=session.required_capabilities,
                model_hint=session.model_hint,
                min_parameter_count_b=session.min_parameter_count_b,
                min_context_tokens=session.min_context_tokens,
                preferred_provider=session.preferred_provider,
                max_tokens=session.max_tokens,
                store_output=session.store_output,
            )
        )
        now = utc_now()
        message = AIChatMessage(
            message_id=message_id_for(session_id, "user", request.prompt, now.isoformat()),
            session_id=session_id,
            role="user",
            content=request.prompt,
            status=ai_request.status.value,
            request_id=ai_request.request_id,
            prompt_hash=sha256_text(request.prompt),
            created_at=now,
            updated_at=now,
        )
        self.put_message(message)
        session = self.refresh_session(session_id)
        self.storage.record_event(
            "ai_chat.message_queued",
            session_id,
            {
                "request_id": ai_request.request_id,
                "message_id": message.message_id,
                "prompt_hash": message.prompt_hash,
                "no_per_task_payment": True,
            },
        )
        return {
            "session": session,
            "message": message,
            "ai_request": ai_request,
        }

    def sync_request(self, session_id: str, request_id: str) -> dict:
        self.get_session(session_id)
        request_message = self.get_message_for_request(session_id, request_id, role="user")
        ai_request = self.queue.get(request_id)
        if request_message.status != ai_request.status.value:
            request_message.status = ai_request.status.value
            request_message.updated_at = utc_now()
            self.put_message(request_message)
        assistant_message = self.get_message_for_request(session_id, request_id, role="assistant", missing_ok=True)
        if ai_request.status == AIRequestStatus.VERIFIED and assistant_message is None:
            now = utc_now()
            assistant_message = AIChatMessage(
                message_id=message_id_for(
                    session_id,
                    "assistant",
                    ai_request.output_hash or ai_request.request_id,
                    now.isoformat(),
                ),
                session_id=session_id,
                role="assistant",
                content=ai_request.output,
                status=ai_request.status.value,
                request_id=ai_request.request_id,
                output_hash=ai_request.output_hash,
                receipt_hash=ai_request.receipt_hash,
                created_at=now,
                updated_at=now,
            )
            self.put_message(assistant_message)
            self.refresh_session(session_id)
            self.storage.record_event(
                "ai_chat.message_verified",
                session_id,
                {
                    "request_id": ai_request.request_id,
                    "message_id": assistant_message.message_id,
                    "output_hash": assistant_message.output_hash,
                    "receipt_hash": assistant_message.receipt_hash,
                    "no_per_task_payment": True,
                },
            )
        return {
            "synced": assistant_message is not None and ai_request.status == AIRequestStatus.VERIFIED,
            "request_status": ai_request.status.value,
            "session": self.get_session(session_id),
            "request_message": request_message,
            "assistant_message": assistant_message,
            "ai_request": ai_request,
        }

    def get_message_for_request(
        self,
        session_id: str,
        request_id: str,
        *,
        role: str,
        missing_ok: bool = False,
    ) -> AIChatMessage | None:
        with self.storage.connect() as connection:
            row = connection.execute(
                """
                SELECT payload
                FROM ai_chat_messages
                WHERE session_id = ?
                  AND request_id = ?
                  AND role = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (session_id, request_id, role),
            ).fetchone()
        if row is None:
            if missing_ok:
                return None
            raise KeyError(f"AI chat message not found for request: {request_id}")
        return AIChatMessage.model_validate(json.loads(row["payload"]))

    def put_message(self, message: AIChatMessage) -> None:
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_chat_messages (
                    message_id,
                    session_id,
                    request_id,
                    role,
                    status,
                    payload,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    status = excluded.status,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    message.message_id,
                    message.session_id,
                    message.request_id,
                    message.role,
                    message.status,
                    message.model_dump_json(),
                    message.created_at.isoformat(),
                    message.updated_at.isoformat(),
                ),
            )

    def refresh_session(self, session_id: str) -> AIChatSession:
        session = self.get_session(session_id)
        with self.storage.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM ai_chat_messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        session.message_count = int(row["count"] if row else 0)
        session.updated_at = utc_now()
        self.put_session(session)
        return session


def normalize_capabilities(values: list[str]) -> list[str]:
    return sorted({value.strip() for value in values if value.strip()})


def build_chat_prompt(session: AIChatSession, messages: list[AIChatMessage], user_prompt: str) -> str:
    lines = [
        "You are Picoin Forge AI, a decentralized L2 AI assistant.",
        "Answer clearly and keep the response useful for the user.",
        f"Session ID: {session.session_id}",
    ]
    for message in messages[-12:]:
        content = (message.content or "").strip()
        if not content:
            continue
        role = "User" if message.role == "user" else "Assistant"
        lines.append(f"{role}: {content}")
    lines.append(f"User: {user_prompt.strip()}")
    lines.append("Assistant:")
    prompt = "\n".join(lines)
    if len(prompt) <= 16000:
        return prompt
    return prompt[-16000:]


def message_id_for(session_id: str, role: str, content: str, stamp: str) -> str:
    return "ai_msg_" + hash_json(
        {
            "session_id": session_id,
            "role": role,
            "content_hash": sha256_text(content),
            "created_at": stamp,
        }
    )[:20]
