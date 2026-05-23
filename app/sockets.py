from collections import defaultdict
import re

from flask import request
from flask_login import current_user
from flask_socketio import emit, join_room

from app import db
from app.models import Message, MessageMention, MessageRead, Room, users_are_blocked, utcnow


ONLINE_USERS = defaultdict(set)


def get_online_user_ids():
    return {user_id for user_id, sids in ONLINE_USERS.items() if sids}


def emit_to_user(socketio, user_id, event_name, payload):
    for sid in tuple(ONLINE_USERS.get(user_id, ())):
        socketio.emit(event_name, payload, to=sid)


def serialize_notification_message_for_user(message, recipient_user):
    return {
        "kind": "message",
        "message_id": message.id,
        "room_id": message.room_id,
        "room_name": message.room.display_name_for(recipient_user),
        "sender_username": message.user.username,
        "preview": message.content,
        "created_at": message.created_at.isoformat(),
        "is_private": message.room.is_private,
    }


def serialize_notification_mention_for_user(message, recipient_user):
    return {
        "kind": "mention",
        "mention_id": f"{message.id}:{recipient_user.id}",
        "message_id": message.id,
        "room_id": message.room_id,
        "room_name": message.room.display_name_for(recipient_user),
        "sender_username": message.user.username,
        "preview": f"You were mentioned by {message.user.username}: {message.content}",
        "created_at": message.created_at.isoformat(),
        "is_private": message.room.is_private,
    }


def extract_mentioned_members(room, message_content):
    if room.is_direct_message or len(room.members) <= 2:
        return []

    usernames = {
        member.user.username.lower(): member.user
        for member in room.members
    }
    mentions = []
    seen_user_ids = set()
    for raw in re.findall(r"@([A-Za-z0-9_]{3,80})", message_content or ""):
        mentioned_user = usernames.get(raw.lower())
        if mentioned_user is None or mentioned_user.id in seen_user_ids:
            continue
        mentions.append(mentioned_user)
        seen_user_ids.add(mentioned_user.id)
    return mentions


def serialize_message_payload(message, current_user_id=None):
    read_entries = sorted(
        message.reads,
        key=lambda entry: entry.read_at or utcnow(),
    )
    read_by = [
        {
            "user_id": entry.user_id,
            "username": entry.user.username,
            "read_at": entry.read_at.isoformat(),
        }
        for entry in read_entries
    ]

    return {
        "id": message.id,
        "room_id": message.room_id,
        "content": message.content,
        "message_type": message.message_type,
        "created_at": message.created_at.isoformat(),
        "user": {
            "id": message.user.id,
            "username": message.user.username,
        },
        "is_own": message.user_id == current_user_id,
        "read_by": read_by,
    }


def serialize_user_status(user):
    return {
        "user_id": user.id,
        "username": user.username,
        "is_online": user.id in get_online_user_ids(),
        "last_seen": user.last_seen.isoformat() if user.last_seen else None,
    }


def get_accessible_room(room_id, user):
    room = db.session.get(Room, int(room_id))
    if room is None:
        return None

    if room.is_private and not room.has_member(user):
        return None

    if room.is_private:
        other_member = room.other_member_for(user)
        if other_member is not None and users_are_blocked(user.id, other_member.id):
            return None

    if not room.is_private and not room.has_member(user):
        room.add_member(user)
        db.session.commit()

    return room


def mark_messages_as_read(room, user):
    newly_read_ids = []

    for message in room.messages:
        if message.user_id == user.id or message.is_read_by(user.id):
            continue

        db.session.add(MessageRead(message=message, user=user))
        newly_read_ids.append(message.id)

    if newly_read_ids:
        user.touch_last_seen()
        db.session.commit()

    return newly_read_ids


def register_socket_events(socketio):
    @socketio.on("connect")
    def handle_connect():
        if not current_user.is_authenticated:
            return False

        current_user.touch_last_seen()
        db.session.commit()
        ONLINE_USERS[current_user.id].add(request.sid)
        emit("presence_snapshot", {"online_user_ids": list(get_online_user_ids())})
        socketio.emit("user_status", serialize_user_status(current_user))

    @socketio.on("join_room")
    def handle_join_room(data):
        if not current_user.is_authenticated:
            emit("error", {"message": "Authentication required."})
            return

        room_id = data.get("room_id")
        if room_id is None:
            emit("error", {"message": "Room id is required."})
            return

        room = get_accessible_room(room_id, current_user)
        if room is None:
            emit("error", {"message": "You do not have access to that room."})
            return

        join_room(room.socket_room)
        newly_read_ids = mark_messages_as_read(room, current_user)
        emit(
            "room_joined",
            {
                "room_id": room.id,
                "room_name": room.display_name_for(current_user),
                "newly_read_ids": newly_read_ids,
            },
        )

    @socketio.on("send_message")
    def handle_send_message(data):
        if not current_user.is_authenticated:
            emit("error", {"message": "Authentication required."})
            return

        room_id = data.get("room_id")
        content = (data.get("content") or "").strip()
        message_type = (data.get("message_type") or "text").strip()

        if room_id is None:
            emit("error", {"message": "Room id is required."})
            return

        if not content:
            emit("error", {"message": "Message content cannot be empty."})
            return

        room = get_accessible_room(room_id, current_user)
        if room is None:
            emit("error", {"message": "You do not have access to that room."})
            return

        message = Message(
            user=current_user,
            room=room,
            content=content,
            message_type=message_type,
        )
        db.session.add(message)
        db.session.flush()
        db.session.add(MessageRead(message=message, user=current_user))
        mentioned_users = extract_mentioned_members(room, content)
        mentioned_user_ids = {
            user.id for user in mentioned_users if user.id != current_user.id
        }
        for mentioned_user in mentioned_users:
            if mentioned_user.id == current_user.id:
                continue
            db.session.add(
                MessageMention(
                    message_id=message.id,
                    user_id=mentioned_user.id,
                )
            )
        current_user.touch_last_seen()
        db.session.commit()
        db.session.refresh(message)

        socketio.emit(
            "receive_message",
            serialize_message_payload(message, current_user.id),
            to=room.socket_room,
        )
        for member in room.members:
            if member.user_id == current_user.id:
                continue
            if member.user_id in mentioned_user_ids:
                emit_to_user(
                    socketio,
                    member.user_id,
                    "notification_item",
                    serialize_notification_mention_for_user(message, member.user),
                )
                continue
            emit_to_user(
                socketio,
                member.user_id,
                "notification_item",
                serialize_notification_message_for_user(message, member.user),
            )

    @socketio.on("typing_start")
    def handle_typing_start(data):
        room_id = data.get("room_id")
        room = get_accessible_room(room_id, current_user) if room_id is not None else None

        if room is None:
            return

        emit(
            "typing_indicator",
            {
                "room_id": room.id,
                "user_id": current_user.id,
                "username": current_user.username,
                "is_typing": True,
            },
            to=room.socket_room,
            skip_sid=request.sid,
        )

    @socketio.on("typing_stop")
    def handle_typing_stop(data):
        room_id = data.get("room_id")
        room = get_accessible_room(room_id, current_user) if room_id is not None else None

        if room is None:
            return

        emit(
            "typing_indicator",
            {
                "room_id": room.id,
                "user_id": current_user.id,
                "username": current_user.username,
                "is_typing": False,
            },
            to=room.socket_room,
            skip_sid=request.sid,
        )

    @socketio.on("mark_read")
    def handle_mark_read(data):
        room_id = data.get("room_id")
        room = get_accessible_room(room_id, current_user) if room_id is not None else None

        if room is None:
            return

        newly_read_ids = mark_messages_as_read(room, current_user)
        if not newly_read_ids:
            return

        emit(
            "message_read",
            {
                "room_id": room.id,
                "user_id": current_user.id,
                "username": current_user.username,
                "message_ids": newly_read_ids,
                "read_at": utcnow().isoformat(),
            },
            to=room.socket_room,
        )

    @socketio.on("disconnect")
    def handle_disconnect():
        if not current_user.is_authenticated:
            return

        user_sids = ONLINE_USERS.get(current_user.id)
        if user_sids and request.sid in user_sids:
            user_sids.remove(request.sid)

        current_user.touch_last_seen()
        db.session.commit()

        if not ONLINE_USERS.get(current_user.id):
            ONLINE_USERS.pop(current_user.id, None)
            socketio.emit("user_status", serialize_user_status(current_user))
