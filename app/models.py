from datetime import datetime, timedelta
from hashlib import sha512
import secrets

from flask import current_app
from flask_login import UserMixin
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.mysql import INTEGER as MySQLInteger
from werkzeug.security import check_password_hash, generate_password_hash

from app import db, login_manager

# PBKDF2-HMAC-SHA512 (salt + iterations); not plain SHA-512 (unsafe for passwords).
PASSWORD_HASH_METHOD = "pbkdf2:sha512"


def utcnow():
    return datetime.utcnow()


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="user", nullable=False, index=True)
    is_email_verified = db.Column(db.Boolean, default=False, nullable=False)
    verification_token_hash = db.Column(db.String(128), nullable=True)
    verification_token_expires_at = db.Column(db.DateTime, nullable=True)
    verification_sent_at = db.Column(db.DateTime, nullable=True)
    last_seen = db.Column(db.DateTime, default=utcnow, nullable=False)
    display_name = db.Column(db.String(120), nullable=True)
    bio = db.Column(db.Text, nullable=True)
    avatar_url = db.Column(db.String(255), nullable=True)

    memberships = db.relationship(
        "RoomMember",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    messages = db.relationship("Message", back_populates="user", lazy="dynamic")
    reads = db.relationship("MessageRead", back_populates="user", lazy="dynamic")

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

    def has_blocked(self, other_user_id):
        return any(block.blocked_id == other_user_id for block in self.blocks_initiated)

    def is_blocked_by(self, other_user_id):
        return any(block.blocker_id == other_user_id for block in self.blocks_received)


class Room(TimestampMixin, db.Model):
    __tablename__ = "rooms"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    is_private = db.Column(db.Boolean, default=False, nullable=False)
    owner_id = db.Column(MySQLInteger(unsigned=True), db.ForeignKey("users.id"), nullable=True, index=True)

    members = db.relationship(
        "RoomMember",
        back_populates="room",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    owner = db.relationship("User", foreign_keys=[owner_id], lazy="joined")
    messages = db.relationship(
        "Message",
        back_populates="room",
        cascade="all, delete-orphan",
        order_by="Message.created_at.asc()",
        lazy="selectin",
    )

    @property
    def socket_room(self):
        return f"room:{self.id}"

    @property
    def is_direct_message(self):
        return self.is_private and self.name.startswith("dm:")

    def has_member(self, user):
        return any(member.user_id == user.id for member in self.members)

    def add_member(self, user):
        if not self.has_member(user):
            self.members.append(RoomMember(user=user))

    def remove_member(self, user):
        membership = next((member for member in self.members if member.user_id == user.id), None)
        if membership is not None:
            db.session.delete(membership)

    def display_name_for(self, current_user):
        if not self.is_direct_message:
            return self.name

        other_member = self.other_member_for(current_user)
        return other_member.username if other_member else self.name

    def other_member_for(self, current_user):
        return next(
            (
                member.user
                for member in self.members
                if member.user_id != current_user.id
            ),
            None,
        )

    @classmethod
    def private_room_name(cls, first_user_id, second_user_id):
        ordered_ids = sorted((first_user_id, second_user_id))
        return f"dm:{ordered_ids[0]}:{ordered_ids[1]}"


class RoomMember(db.Model):
    __tablename__ = "room_members"
    __table_args__ = (
        db.UniqueConstraint("user_id", "room_id", name="uq_room_member_user_room"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey("rooms.id"), nullable=False)
    joined_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    user = db.relationship("User", back_populates="memberships", lazy="joined")
    room = db.relationship("Room", back_populates="members")


class Message(TimestampMixin, db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey("rooms.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    message_type = db.Column(db.String(50), default="text", nullable=False)

    user = db.relationship("User", back_populates="messages", lazy="joined")
    room = db.relationship("Room", back_populates="messages")
    reads = db.relationship(
        "MessageRead",
        back_populates="message",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def is_read_by(self, user_id):
        return any(read.user_id == user_id for read in self.reads)


class MessageRead(db.Model):
    __tablename__ = "message_reads"
    __table_args__ = (
        db.UniqueConstraint("message_id", "user_id", name="uq_message_read_user"),
    )

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    read_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    message = db.relationship("Message", back_populates="reads")
    user = db.relationship("User", back_populates="reads", lazy="joined")


class MessageMention(db.Model):
    __tablename__ = "message_mentions"
    __table_args__ = (
        db.UniqueConstraint("message_id", "user_id", name="uq_message_mention_user"),
    )

    id = db.Column(MySQLInteger(unsigned=True), primary_key=True)
    message_id = db.Column(MySQLInteger(unsigned=True), db.ForeignKey("messages.id"), nullable=False, index=True)
    user_id = db.Column(MySQLInteger(unsigned=True), db.ForeignKey("users.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    message = db.relationship("Message", lazy="joined")
    user = db.relationship("User", lazy="joined")


class UserBlock(db.Model):
    __tablename__ = "user_blocks"
    __table_args__ = (
        db.UniqueConstraint("blocker_id", "blocked_id", name="uq_user_block_pair"),
    )

    id = db.Column(MySQLInteger(unsigned=True), primary_key=True)
    blocker_id = db.Column(MySQLInteger(unsigned=True), db.ForeignKey("users.id"), nullable=False, index=True)
    blocked_id = db.Column(MySQLInteger(unsigned=True), db.ForeignKey("users.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    blocker = db.relationship(
        "User",
        foreign_keys=[blocker_id],
        backref=db.backref("blocks_initiated", lazy="selectin", cascade="all, delete-orphan"),
        lazy="joined",
    )
    blocked = db.relationship(
        "User",
        foreign_keys=[blocked_id],
        backref=db.backref("blocks_received", lazy="selectin", cascade="all, delete-orphan"),
        lazy="joined",
    )


class RoomInvitation(db.Model):
    __tablename__ = "room_invitations"
    __table_args__ = (
        # One lifecycle row per (room, invitee); status is updated on accept/decline/re-invite.
        db.UniqueConstraint("room_id", "invitee_id", name="uq_room_invitation_room_invitee"),
    )

    id = db.Column(MySQLInteger(unsigned=True), primary_key=True)
    room_id = db.Column(MySQLInteger(unsigned=True), db.ForeignKey("rooms.id"), nullable=False, index=True)
    inviter_id = db.Column(MySQLInteger(unsigned=True), db.ForeignKey("users.id"), nullable=False, index=True)
    invitee_id = db.Column(MySQLInteger(unsigned=True), db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.String(20), default="pending", nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    responded_at = db.Column(db.DateTime, nullable=True)

    room = db.relationship("Room", lazy="joined")
    inviter = db.relationship("User", foreign_keys=[inviter_id], lazy="joined")
    invitee = db.relationship("User", foreign_keys=[invitee_id], lazy="joined")


class AdminAuditLog(db.Model):
    __tablename__ = "admin_audit_logs"

    id = db.Column(MySQLInteger(unsigned=True), primary_key=True)
    actor_id = db.Column(MySQLInteger(unsigned=True), db.ForeignKey("users.id"), nullable=True, index=True)
    actor_role = db.Column(db.String(20), nullable=False, index=True)
    action = db.Column(db.String(120), nullable=False, index=True)
    target_type = db.Column(db.String(60), nullable=True, index=True)
    target_id = db.Column(MySQLInteger(unsigned=True), nullable=True, index=True)
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    actor = db.relationship("User", lazy="joined")


def users_are_blocked(first_user_id, second_user_id):
    return db.session.scalar(
        select(UserBlock.id).where(
            or_(
                and_(
                    UserBlock.blocker_id == first_user_id,
                    UserBlock.blocked_id == second_user_id,
                ),
                and_(
                    UserBlock.blocker_id == second_user_id,
                    UserBlock.blocked_id == first_user_id,
                ),
            )
        )
    ) is not None


def get_or_create_private_room(current_user, other_user):
    room_name = Room.private_room_name(current_user.id, other_user.id)
    room = db.session.scalar(select(Room).where(Room.name == room_name))

    if room is None:
        room = Room(name=room_name, is_private=True)
        room.add_member(current_user)
        room.add_member(other_user)
        db.session.add(room)
        db.session.flush()

    return room
