from datetime import datetime, timedelta
from hashlib import sha512
import secrets

from flask import current_app
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app import login_manager
from app import repository as repo

PASSWORD_HASH_METHOD = "pbkdf2:sha512"


def utcnow():
    return datetime.utcnow()


@login_manager.user_loader
def load_user(user_id):
    return User.from_row(repo.get_user_by_id(int(user_id)))


class User(UserMixin):
    def __init__(
        self,
        id=None,
        username=None,
        email=None,
        password_hash=None,
        role="user",
        is_email_verified=False,
        verification_token_hash=None,
        verification_token_expires_at=None,
        verification_sent_at=None,
        last_seen=None,
        display_name=None,
        bio=None,
        avatar_url=None,
        created_at=None,
        memberships=None,
        blocks_initiated=None,
        blocks_received=None,
    ):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.role = role or "user"
        self.is_email_verified = bool(is_email_verified)
        self.verification_token_hash = verification_token_hash
        self.verification_token_expires_at = verification_token_expires_at
        self.verification_sent_at = verification_sent_at
        self.last_seen = last_seen
        self.display_name = display_name
        self.bio = bio
        self.avatar_url = avatar_url
        self.created_at = created_at
        self.memberships = memberships or []
        self.blocks_initiated = blocks_initiated or []
        self.blocks_received = blocks_received or []

    @classmethod
    def from_row(cls, row):
        if row is None:
            return None
        return cls(
            id=row["id"],
            username=row["username"],
            email=row["email"],
            password_hash=row.get("password_hash"),
            role=row.get("role", "user"),
            is_email_verified=row.get("is_email_verified", False),
            verification_token_hash=row.get("verification_token_hash"),
            verification_token_expires_at=row.get("verification_token_expires_at"),
            verification_sent_at=row.get("verification_sent_at"),
            last_seen=row.get("last_seen"),
            display_name=row.get("display_name"),
            bio=row.get("bio"),
            avatar_url=row.get("avatar_url"),
            created_at=row.get("created_at"),
        )

    @property
    def is_admin(self):
        return self.role in {"admin", "super_admin"}

    @property
    def is_super_admin(self):
        return self.role == "super_admin"

    def set_password(self, password):
        self.password_hash = generate_password_hash(
            password,
            method=PASSWORD_HASH_METHOD,
        )

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def touch_last_seen(self):
        self.last_seen = utcnow()
        if self.id is not None:
            repo.touch_user_last_seen(self.id, self.last_seen)

    def generate_email_verification_code(self):
        raw_code = f"{secrets.randbelow(1000000):06d}"
        self.verification_token_hash = sha512(raw_code.encode("utf-8")).hexdigest()
        self.verification_sent_at = utcnow()
        self.verification_token_expires_at = self.verification_sent_at + timedelta(
            seconds=current_app.config["VERIFICATION_TOKEN_TTL_SECONDS"]
        )
        self.is_email_verified = False
        return raw_code

    def verify_email_code(self, raw_code):
        if not raw_code or not self.verification_token_hash:
            return False

        normalized_code = str(raw_code).strip()
        if len(normalized_code) != 6 or not normalized_code.isdigit():
            return False

        token_hash = sha512(normalized_code.encode("utf-8")).hexdigest()
        if token_hash != self.verification_token_hash:
            return False

        if self.verification_token_expires_at and self.verification_token_expires_at < utcnow():
            return False

        self.is_email_verified = True
        self.verification_token_hash = None
        self.verification_token_expires_at = None
        return True

    def clear_verification_state(self):
        self.verification_token_hash = None
        self.verification_token_expires_at = None
        self.verification_sent_at = None

    def set_role(self, role):
        allowed_roles = {"user", "admin", "super_admin"}
        normalized_role = (role or "").strip().lower()
        self.role = normalized_role if normalized_role in allowed_roles else "user"

    @property
    def profile_name(self):
        return self.display_name or self.username

    def load_blocks(self):
        self.blocks_initiated = [
            UserBlock.from_row(row) for row in repo.list_blocks_initiated_by(self.id)
        ]
        self.blocks_received = [
            UserBlock.from_row(row) for row in repo.list_blocks_received_by(self.id)
        ]

    def has_blocked(self, other_user_id):
        if not self.blocks_initiated:
            self.load_blocks()
        return any(block.blocked_id == other_user_id for block in self.blocks_initiated)

    def is_blocked_by(self, other_user_id):
        if not self.blocks_received:
            self.load_blocks()
        return any(block.blocker_id == other_user_id for block in self.blocks_received)

    def save(self):
        if self.id is None:
            self.id = repo.insert_user(
                username=self.username,
                email=self.email,
                password_hash=self.password_hash,
                role=self.role,
                is_email_verified=self.is_email_verified,
                verification_token_hash=self.verification_token_hash,
                verification_token_expires_at=self.verification_token_expires_at,
                verification_sent_at=self.verification_sent_at,
                last_seen=self.last_seen or utcnow(),
                display_name=self.display_name,
                bio=self.bio,
                avatar_url=self.avatar_url,
                created_at=self.created_at or utcnow(),
            )
            return

        repo.update_user(
            self.id,
            username=self.username,
            email=self.email,
            password_hash=self.password_hash,
            role=self.role,
            is_email_verified=self.is_email_verified,
            verification_token_hash=self.verification_token_hash,
            verification_token_expires_at=self.verification_token_expires_at,
            verification_sent_at=self.verification_sent_at,
            last_seen=self.last_seen,
            display_name=self.display_name,
            bio=self.bio,
            avatar_url=self.avatar_url,
            clear_verification=(
                self.verification_token_hash is None
                and self.verification_token_expires_at is None
                and self.verification_sent_at is None
            ),
        )

    def delete(self):
        if self.id is not None:
            repo.delete_user(self.id)


class Room:
    def __init__(
        self,
        id=None,
        name=None,
        is_private=False,
        owner_id=None,
        created_at=None,
        members=None,
        owner=None,
        messages=None,
    ):
        self.id = id
        self.name = name
        self.is_private = bool(is_private)
        self.owner_id = owner_id
        self.created_at = created_at
        self.members = members or []
        self.owner = owner
        self.messages = messages or []

    @classmethod
    def from_row(cls, row, members=None, owner=None, messages=None):
        if row is None:
            return None
        return cls(
            id=row["id"],
            name=row["name"],
            is_private=row.get("is_private", False),
            owner_id=row.get("owner_id"),
            created_at=row.get("created_at"),
            members=members or [],
            owner=owner,
            messages=messages or [],
        )

    @classmethod
    def get(cls, room_id, *, with_members=False, with_messages=False, with_owner=False):
        row = repo.get_room_by_id(room_id)
        if row is None:
            return None
        members = []
        owner = None
        messages = []
        if with_members:
            members = [
                RoomMember.from_member_row(member_row)
                for member_row in repo.list_room_members(room_id)
            ]
        if with_owner and row.get("owner_id"):
            owner = User.from_row(repo.get_user_by_id(row["owner_id"]))
        if with_messages:
            messages = [
                Message.from_message_bundle(bundle)
                for bundle in repo.fetch_room_with_members_and_messages(room_id)["messages"]
            ]
        return cls.from_row(row, members=members, owner=owner, messages=messages)

    @property
    def socket_room(self):
        return f"room:{self.id}"

    @property
    def is_direct_message(self):
        return self.is_private and self.name.startswith("dm:")

    def has_member(self, user):
        user_id = user.id if hasattr(user, "id") else user
        if self.members:
            return any(member.user_id == user_id for member in self.members)
        return repo.is_room_member(self.id, user_id)

    def load_members(self):
        self.members = [
            RoomMember.from_member_row(member_row)
            for member_row in repo.list_room_members(self.id)
        ]

    def load_messages(self):
        bundle = repo.fetch_room_with_members_and_messages(self.id)
        self.messages = [
            Message.from_message_bundle(message_bundle)
            for message_bundle in bundle["messages"]
        ]

    def add_member(self, user):
        user_id = user.id if hasattr(user, "id") else user
        if not self.has_member(user_id):
            repo.add_room_member(self.id, user_id)
            if self.members is not None:
                member_user = user if isinstance(user, User) else User.from_row(repo.get_user_by_id(user_id))
                self.members.append(RoomMember(user_id=user_id, room_id=self.id, user=member_user))

    def remove_member(self, user):
        user_id = user.id if hasattr(user, "id") else user
        repo.remove_room_member(self.id, user_id)
        self.members = [member for member in self.members if member.user_id != user_id]

    def display_name_for(self, current_user):
        if not self.is_direct_message:
            return self.name

        other_member = self.other_member_for(current_user)
        return other_member.username if other_member else self.name

    def other_member_for(self, current_user):
        if not self.members:
            self.load_members()
        current_user_id = current_user.id if hasattr(current_user, "id") else current_user
        for member in self.members:
            if member.user_id != current_user_id:
                return member.user
        return None

    def save(self):
        if self.id is None:
            self.id = repo.insert_room(
                self.name,
                is_private=self.is_private,
                owner_id=self.owner_id,
                created_at=self.created_at or utcnow(),
            )
            return
        repo.update_room(
            self.id,
            name=self.name,
            is_private=self.is_private,
            owner_id=self.owner_id,
        )

    def delete(self):
        if self.id is not None:
            repo.delete_room_invitations_for_room(self.id)
            repo.delete_room(self.id)

    @classmethod
    def private_room_name(cls, first_user_id, second_user_id):
        ordered_ids = sorted((first_user_id, second_user_id))
        return f"dm:{ordered_ids[0]}:{ordered_ids[1]}"


class RoomMember:
    def __init__(self, id=None, user_id=None, room_id=None, joined_at=None, user=None, room=None):
        self.id = id
        self.user_id = user_id
        self.room_id = room_id
        self.joined_at = joined_at
        self.user = user
        self.room = room

    @classmethod
    def from_member_row(cls, row):
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            room_id=row["room_id"],
            joined_at=row.get("joined_at"),
            user=User.from_row(row.get("user")),
        )


class Message:
    def __init__(
        self,
        id=None,
        user_id=None,
        room_id=None,
        content=None,
        message_type="text",
        created_at=None,
        user=None,
        room=None,
        reads=None,
    ):
        if user is not None and user_id is None:
            user_id = user.id
        if room is not None and room_id is None:
            room_id = room.id
        self.id = id
        self.user_id = user_id
        self.room_id = room_id
        self.content = content
        self.message_type = message_type or "text"
        self.created_at = created_at
        self.user = user
        self.room = room
        self.reads = reads or []

    @classmethod
    def from_row(cls, row, user=None, room=None, reads=None):
        if row is None:
            return None
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            room_id=row["room_id"],
            content=row["content"],
            message_type=row.get("message_type", "text"),
            created_at=row.get("created_at"),
            user=user,
            room=room,
            reads=reads or [],
        )

    @classmethod
    def from_message_bundle(cls, bundle):
        reads = [
            MessageRead.from_row(
                {
                    "id": read_row.get("id"),
                    "message_id": read_row["message_id"],
                    "user_id": read_row["user_id"],
                    "read_at": read_row["read_at"],
                },
                user=User.from_row(
                    {"id": read_row["user_id"], "username": read_row["username"]}
                ),
            )
            for read_row in bundle.get("reads", [])
        ]
        return cls.from_row(
            bundle["message"],
            user=User.from_row(bundle.get("user")),
            reads=reads,
        )

    @classmethod
    def get(cls, message_id, *, with_details=False):
        if not with_details:
            return cls.from_row(repo.get_message_by_id(message_id))
        bundle = repo.fetch_message_with_details(message_id)
        if bundle is None:
            return None
        room = Room.from_row(bundle["room"], members=[
            RoomMember.from_member_row(member_row) for member_row in bundle["members"]
        ])
        return cls.from_row(
            bundle["message"],
            user=User.from_row(bundle["user"]),
            room=room,
            reads=[
                MessageRead.from_row(
                    read_row,
                    user=User.from_row(
                        {"id": read_row["user_id"], "username": read_row["username"]}
                    ),
                )
                for read_row in bundle["reads"]
            ],
        )

    def is_read_by(self, user_id):
        if self.reads:
            return any(read.user_id == user_id for read in self.reads)
        return repo.is_message_read_by_user(self.id, user_id)

    def save(self):
        if self.id is None:
            self.id = repo.insert_message(
                self.user_id,
                self.room_id,
                self.content,
                message_type=self.message_type,
                created_at=self.created_at or utcnow(),
            )
            return
        raise NotImplementedError("Message updates are not supported.")

    def reload(self):
        refreshed = self.get(self.id, with_details=True)
        if refreshed is None:
            return
        self.__dict__.update(refreshed.__dict__)


class MessageRead:
    def __init__(self, id=None, message_id=None, user_id=None, read_at=None, message=None, user=None):
        if message is not None and message_id is None:
            message_id = message.id
        if user is not None and user_id is None:
            user_id = user.id
        self.id = id
        self.message_id = message_id
        self.user_id = user_id
        self.read_at = read_at
        self.message = message
        self.user = user

    @classmethod
    def from_row(cls, row, message=None, user=None):
        if row is None:
            return None
        return cls(
            id=row.get("id"),
            message_id=row["message_id"],
            user_id=row["user_id"],
            read_at=row.get("read_at"),
            message=message,
            user=user,
        )

    def save(self):
        repo.insert_message_read(
            self.message_id,
            self.user_id,
            read_at=self.read_at or utcnow(),
        )


class MessageMention:
    def __init__(self, id=None, message_id=None, user_id=None, created_at=None, message=None, user=None):
        self.id = id
        self.message_id = message_id
        self.user_id = user_id
        self.created_at = created_at
        self.message = message
        self.user = user

    @classmethod
    def from_row(cls, row, message=None, user=None):
        if row is None:
            return None
        return cls(
            id=row.get("id"),
            message_id=row["message_id"],
            user_id=row["user_id"],
            created_at=row.get("created_at"),
            message=message,
            user=user,
        )

    def save(self):
        repo.insert_message_mention(
            self.message_id,
            self.user_id,
            created_at=self.created_at or utcnow(),
        )


class UserBlock:
    def __init__(self, id=None, blocker_id=None, blocked_id=None, created_at=None, blocker=None, blocked=None):
        self.id = id
        self.blocker_id = blocker_id
        self.blocked_id = blocked_id
        self.created_at = created_at
        self.blocker = blocker
        self.blocked = blocked

    @classmethod
    def from_row(cls, row, blocker=None, blocked=None):
        if row is None:
            return None
        return cls(
            id=row.get("id"),
            blocker_id=row["blocker_id"],
            blocked_id=row["blocked_id"],
            created_at=row.get("created_at"),
            blocker=blocker,
            blocked=blocked,
        )

    def save(self):
        repo.insert_user_block(self.blocker_id, self.blocked_id, created_at=self.created_at)

    def delete(self):
        repo.delete_user_block(self.blocker_id, self.blocked_id)


class RoomInvitation:
    def __init__(
        self,
        id=None,
        room_id=None,
        inviter_id=None,
        invitee_id=None,
        status="pending",
        created_at=None,
        responded_at=None,
        room=None,
        inviter=None,
        invitee=None,
    ):
        self.id = id
        self.room_id = room_id
        self.inviter_id = inviter_id
        self.invitee_id = invitee_id
        self.status = status
        self.created_at = created_at
        self.responded_at = responded_at
        self.room = room
        self.inviter = inviter
        self.invitee = invitee

    @classmethod
    def from_row(cls, row, room=None, inviter=None, invitee=None):
        if row is None:
            return None
        return cls(
            id=row["id"],
            room_id=row["room_id"],
            inviter_id=row["inviter_id"],
            invitee_id=row["invitee_id"],
            status=row.get("status", "pending"),
            created_at=row.get("created_at"),
            responded_at=row.get("responded_at"),
            room=room,
            inviter=inviter,
            invitee=invitee,
        )

    @classmethod
    def get(cls, invitation_id, *, with_details=False):
        if not with_details:
            return cls.from_row(repo.get_room_invitation_by_id(invitation_id))
        bundle = repo.fetch_room_invitation_with_details(invitation_id)
        if bundle is None:
            return None
        room = Room.from_row(
            bundle["room"],
            members=[RoomMember.from_member_row(member_row) for member_row in bundle["members"]],
        )
        return cls.from_row(
            bundle["invitation"],
            room=room,
            inviter=User.from_row(bundle["inviter"]),
            invitee=User.from_row(bundle["invitee"]),
        )

    def reload(self):
        refreshed = self.get(self.id, with_details=True)
        if refreshed is None:
            return
        self.__dict__.update(refreshed.__dict__)

    def save(self):
        if self.id is None:
            self.id = repo.insert_room_invitation(
                self.room_id,
                self.inviter_id,
                self.invitee_id,
                status=self.status,
                created_at=self.created_at or utcnow(),
            )
            return
        repo.update_room_invitation(
            self.id,
            inviter_id=self.inviter_id,
            status=self.status,
            responded_at=self.responded_at,
            created_at=self.created_at,
        )

    def delete(self):
        if self.id is not None:
            repo.delete_room_invitation(self.id)


class AdminAuditLog:
    def __init__(
        self,
        id=None,
        actor_id=None,
        actor_role=None,
        action=None,
        target_type=None,
        target_id=None,
        details=None,
        created_at=None,
        actor=None,
        actor_username=None,
    ):
        self.id = id
        self.actor_id = actor_id
        self.actor_role = actor_role
        self.action = action
        self.target_type = target_type
        self.target_id = target_id
        self.details = details
        self.created_at = created_at
        self.actor = actor
        self.actor_username = actor_username

    @classmethod
    def from_row(cls, row, actor=None):
        if row is None:
            return None
        actor_username = row.get("actor_username")
        resolved_actor = actor
        if resolved_actor is None and row.get("actor_id") and actor_username:
            resolved_actor = User.from_row(
                {"id": row["actor_id"], "username": actor_username}
            )
        return cls(
            id=row.get("id"),
            actor_id=row.get("actor_id"),
            actor_role=row.get("actor_role"),
            action=row.get("action"),
            target_type=row.get("target_type"),
            target_id=row.get("target_id"),
            details=row.get("details"),
            created_at=row.get("created_at"),
            actor=resolved_actor,
            actor_username=actor_username,
        )

    def save(self):
        repo.insert_admin_audit_log(
            self.actor_id,
            self.actor_role,
            self.action,
            target_type=self.target_type,
            target_id=self.target_id,
            details=self.details,
            created_at=self.created_at or utcnow(),
        )


def users_are_blocked(first_user_id, second_user_id):
    return repo.users_are_blocked(first_user_id, second_user_id)


def get_or_create_private_room(current_user, other_user):
    room_name = Room.private_room_name(current_user.id, other_user.id)
    row = repo.get_room_by_name(room_name)

    if row is None:
        room = Room(name=room_name, is_private=True)
        room.save()
        room.add_member(current_user)
        room.add_member(other_user)
        return room

    room = Room.from_row(row)
    room.load_members()
    if not room.has_member(current_user):
        room.add_member(current_user)
    if not room.has_member(other_user):
        room.add_member(other_user)
    return room
