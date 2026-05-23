import os
from datetime import datetime, timedelta
from functools import wraps
from uuid import uuid4

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload, selectinload
from werkzeug.utils import secure_filename

from app import db, socketio
from app.forms import (
    ActionForm,
    AdminUserForm,
    LoginForm,
    PrivateChatForm,
    UserProfileForm,
    PrivateRoomForm,
    RegisterForm,
    ResendVerificationForm,
    RoomInviteForm,
    RoomForm,
    VerifyEmailForm,
)
from app.mail import send_verification_email
from app.models import AdminAuditLog, Message, MessageMention, MessageRead, Room, RoomInvitation, RoomMember, User, UserBlock, get_or_create_private_room, users_are_blocked, utcnow
from app.sockets import emit_to_user, get_accessible_room, get_online_user_ids, serialize_message_payload, serialize_notification_message_for_user


main_bp = Blueprint("main", __name__)


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped_view


def ensure_general_room():
    room = db.session.scalar(select(Room).where(Room.name == "General"))
    if room is None:
        room = Room(name="General", is_private=False)
        db.session.add(room)
        db.session.commit()
    return room


def ensure_room_membership(user, room, commit=False):
    if not room.has_member(user):
        room.add_member(user)
        if commit:
            db.session.commit()


def mark_room_messages_as_read(room, user):
    newly_read_ids = []

    for message in room.messages:
        if message.user_id == user.id or message.is_read_by(user.id):
            continue

        db.session.add(MessageRead(message=message, user=user))
        newly_read_ids.append(message.id)

    if newly_read_ids:
        db.session.commit()

    return newly_read_ids


def build_room_lists(user):
    public_rooms = db.session.scalars(
        select(Room).where(Room.is_private.is_(False)).order_by(Room.name.asc())
    ).all()
    raw_private_rooms = db.session.scalars(
        select(Room)
        .join(RoomMember, RoomMember.room_id == Room.id)
        .where(Room.is_private.is_(True), RoomMember.user_id == user.id)
        .order_by(Room.created_at.desc())
    ).all()
    private_rooms = []

    for room in raw_private_rooms:
        if room.is_direct_message:
            other_member = room.other_member_for(user)
            if other_member and not users_are_blocked(user.id, other_member.id):
                private_rooms.append(room)
            continue
        private_rooms.append(room)

    return public_rooms, private_rooms


def build_block_maps(user):
    blocked_ids = {
        block.blocked_id
        for block in db.session.scalars(
            select(UserBlock).where(UserBlock.blocker_id == user.id)
        ).all()
    }
    blocked_by_ids = {
        block.blocker_id
        for block in db.session.scalars(
            select(UserBlock).where(UserBlock.blocked_id == user.id)
        ).all()
    }
    return blocked_ids, blocked_by_ids


def private_room_is_blocked(room, user):
    if not room or not room.is_direct_message:
        return False

    other_member = room.other_member_for(user)
    return other_member is not None and users_are_blocked(user.id, other_member.id)


def public_room_can_be_deleted(room):
    return room is not None and not room.is_private and room.name != "General"


def private_room_can_be_deleted_by_user(room, user):
    return (
        room is not None
        and room.is_private
        and not room.is_direct_message
        and room.owner_id == user.id
    )


def room_can_be_deleted_by_user(room, user):
    return public_room_can_be_deleted(room) or private_room_can_be_deleted_by_user(room, user)


def can_manage_private_room(room, user):
    return (
        room is not None
        and room.is_private
        and not room.is_direct_message
        and room.has_member(user)
        and (room.owner_id == user.id or room.owner_id is None)
    )


def get_media_message_type(filename):
    extension = os.path.splitext(filename)[1].lower()
    if extension in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]:
        return "image"
    if extension in current_app.config["ALLOWED_VIDEO_EXTENSIONS"]:
        return "video"
    return None


def save_uploaded_media(file_storage):
    original_filename = secure_filename(file_storage.filename or "")
    if not original_filename:
        raise ValueError("Choose an image or video to upload.")

    message_type = get_media_message_type(original_filename)
    if message_type is None:
        raise ValueError("Only JPG, PNG, GIF, WEBP, MP4, WEBM, or MOV files are allowed.")

    upload_root = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_root, exist_ok=True)

    extension = os.path.splitext(original_filename)[1].lower()
    stored_filename = f"{uuid4().hex}{extension}"
    file_storage.save(os.path.join(upload_root, stored_filename))

    return (
        message_type,
        url_for(
            "static",
            filename=f"{current_app.config['MEDIA_UPLOAD_SUBDIR']}/{stored_filename}",
        ),
    )


def save_uploaded_profile_avatar(file_storage):
    original_filename = secure_filename(file_storage.filename or "")
    if not original_filename:
        raise ValueError("Choose an image to upload.")

    extension = os.path.splitext(original_filename)[1].lower()
    if extension not in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]:
        raise ValueError("Only JPG, PNG, GIF, or WEBP images are allowed for profile avatars.")

    upload_root = current_app.config["PROFILE_AVATAR_UPLOAD_FOLDER"]
    os.makedirs(upload_root, exist_ok=True)

    stored_filename = f"{uuid4().hex}{extension}"
    file_storage.save(os.path.join(upload_root, stored_filename))
    return url_for(
        "static",
        filename=f"{current_app.config['PROFILE_AVATAR_SUBDIR']}/{stored_filename}",
    )


def delete_local_profile_avatar_if_any(avatar_url):
    if not avatar_url:
        return
    relative_subdir = current_app.config["PROFILE_AVATAR_SUBDIR"]
    expected_prefix = url_for("static", filename=f"{relative_subdir}/")
    if not avatar_url.startswith(expected_prefix):
        return

    filename = avatar_url[len(expected_prefix):]
    if not filename:
        return

    avatar_path = os.path.join(
        current_app.config["PROFILE_AVATAR_UPLOAD_FOLDER"],
        secure_filename(filename),
    )
    if os.path.isfile(avatar_path):
        os.remove(avatar_path)


def serialize_public_profile(user):
    return {
        "user_id": user.id,
        "username": user.username,
        "profile_name": user.profile_name,
        "avatar_url": user.avatar_url,
        "bio": user.bio,
    }


def unread_messages_statement_for_user(user):
    read_exists = select(MessageRead.id).where(
        MessageRead.message_id == Message.id,
        MessageRead.user_id == user.id,
    )
    mention_exists = select(MessageMention.id).where(
        MessageMention.message_id == Message.id,
        MessageMention.user_id == user.id,
    )
    return (
        select(Message)
        .join(RoomMember, RoomMember.room_id == Message.room_id)
        .where(
            RoomMember.user_id == user.id,
            Message.user_id != user.id,
            ~read_exists.exists(),
            ~mention_exists.exists(),
        )
    )


def unread_mentions_statement_for_user(user):
    read_exists = select(MessageRead.id).where(
        MessageRead.message_id == MessageMention.message_id,
        MessageRead.user_id == user.id,
    )
    return (
        select(MessageMention)
        .where(
            MessageMention.user_id == user.id,
            ~read_exists.exists(),
        )
    )


def pending_room_invites_statement_for_user(user):
    return (
        select(RoomInvitation)
        .where(
            RoomInvitation.invitee_id == user.id,
            RoomInvitation.status == "pending",
        )
    )


def serialize_room_invitation_notification(invitation, recipient_user):
    return {
        "kind": "invitation",
        "invitation_id": invitation.id,
        "room_id": invitation.room_id,
        "room_name": invitation.room.display_name_for(recipient_user),
        "sender_username": invitation.inviter.username,
        "preview": f"{invitation.inviter.username} invited you to join {invitation.room.display_name_for(recipient_user)}.",
        "created_at": invitation.created_at,
        "is_private": invitation.room.is_private,
        "status": invitation.status,
    }


def serialize_room_members_payload(room):
    return {
        "room_id": room.id,
        "members": [
            {
                "user_id": member.user.id,
                "username": member.user.username,
            }
            for member in room.members
        ],
    }


def get_unread_notification_count(user):
    return len(build_notification_items(user, limit=None))


def build_unread_notifications(user, limit=50):
    statement = (
        unread_messages_statement_for_user(user)
        .options(
            joinedload(Message.user),
            selectinload(Message.room)
            .selectinload(Room.members)
            .selectinload(RoomMember.user),
        )
        .order_by(Message.created_at.desc())
    )
    if limit is not None:
        statement = statement.limit(limit)

    messages = db.session.execute(statement).scalars().unique().all()
    notifications = []

    for message in messages:
        room = message.room
        if room is None:
            continue
        if room.is_private and private_room_is_blocked(room, user):
            continue

        notifications.append(
            {
                "message_id": message.id,
                "kind": "message",
                "room_id": room.id,
                "room_name": room.display_name_for(user),
                "sender_username": message.user.username,
                "preview": message.content,
                "created_at": message.created_at,
                "is_private": room.is_private,
            }
        )

    return notifications


def build_mention_notifications(user, limit=50):
    statement = (
        unread_mentions_statement_for_user(user)
        .options(
            joinedload(MessageMention.message)
            .joinedload(Message.user),
            joinedload(MessageMention.message)
            .joinedload(Message.room)
            .selectinload(Room.members)
            .selectinload(RoomMember.user),
        )
        .order_by(MessageMention.created_at.desc())
    )
    if limit is not None:
        statement = statement.limit(limit)

    mentions = db.session.execute(statement).scalars().all()
    notifications = []
    for mention in mentions:
        message = mention.message
        if message is None or message.room is None:
            continue
        room = message.room
        if room.is_private and private_room_is_blocked(room, user):
            continue
        notifications.append(
            {
                "mention_id": mention.id,
                "kind": "mention",
                "message_id": message.id,
                "room_id": room.id,
                "room_name": room.display_name_for(user),
                "sender_username": message.user.username,
                "preview": f"You were mentioned by {message.user.username}: {message.content}",
                "created_at": mention.created_at,
                "is_private": room.is_private,
            }
        )

    return notifications


def build_room_invitation_notifications(user, limit=50):
    statement = (
        pending_room_invites_statement_for_user(user)
        .options(
            joinedload(RoomInvitation.room)
            .selectinload(Room.members)
            .selectinload(RoomMember.user),
            joinedload(RoomInvitation.inviter),
        )
        .order_by(RoomInvitation.created_at.desc())
    )
    if limit is not None:
        statement = statement.limit(limit)

    invitations = db.session.execute(statement).scalars().all()
    return [
        serialize_room_invitation_notification(invitation, user)
        for invitation in invitations
    ]


def build_notification_items(user, limit=50):
    message_items = build_unread_notifications(user, limit=None)
    mention_items = build_mention_notifications(user, limit=None)
    invitation_items = build_room_invitation_notifications(user, limit=None)
    items = message_items + mention_items + invitation_items
    items.sort(key=lambda item: item["created_at"], reverse=True)
    if limit is None:
        return items
    return items[:limit]


def emit_notification_item_for_user(user, item):
    payload = dict(item)
    created_at = payload.get("created_at")
    if hasattr(created_at, "isoformat"):
        payload["created_at"] = created_at.isoformat()
    emit_to_user(socketio, user.id, "notification_item", payload)


def emit_notification_refresh_for_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        return
    emit_to_user(
        socketio,
        user_id,
        "notification_count",
        {"count": get_unread_notification_count(user)},
    )


def build_dashboard_context(selected_room_id=None):
    ensure_general_room()

    delete_room_form = ActionForm(prefix="delete-room")
    room_form = RoomForm(prefix="room")
    private_room_form = PrivateRoomForm(prefix="private-room")
    room_invite_form = RoomInviteForm(prefix="invite-room")
    private_chat_form = PrivateChatForm(prefix="private")
    public_rooms, private_rooms = build_room_lists(current_user)
    blocked_ids, blocked_by_ids = build_block_maps(current_user)
    all_users = db.session.scalars(
        select(User)
        .where(User.id != current_user.id)
        .order_by(User.username.asc())
    ).all()

    selected_room = None
    initial_messages = []

    if selected_room_id is not None:
        selected_room = db.session.get(Room, selected_room_id)
        if selected_room is None:
            abort(404)

        if selected_room.is_private and not selected_room.has_member(current_user):
            abort(403)

        if private_room_is_blocked(selected_room, current_user):
            flash("This direct chat is unavailable because one of you has blocked the other.", "error")
            return {
                "delete_room_form": delete_room_form,
                "room_form": room_form,
                "private_room_form": private_room_form,
                "room_invite_form": room_invite_form,
                "private_chat_form": private_chat_form,
                "public_rooms": public_rooms,
                "private_rooms": private_rooms,
                "all_users": all_users,
                "current_room": None,
                "initial_messages": [],
                "online_user_ids": list(get_online_user_ids()),
                "blocked_ids": blocked_ids,
                "blocked_by_ids": blocked_by_ids,
                "selected_room_other_user": None,
                "removable_members": [],
                "current_room_is_blocked": False,
                "current_room_block_reason": None,
            }

        if not selected_room.is_private:
            ensure_room_membership(current_user, selected_room, commit=True)

        mark_room_messages_as_read(selected_room, current_user)
        db.session.refresh(selected_room)
        initial_messages = [
            serialize_message_payload(message, current_user.id)
            for message in selected_room.messages
        ]

    selected_room_other_user = (
        selected_room.other_member_for(current_user)
        if selected_room and selected_room.is_direct_message
        else None
    )
    current_room_is_blocked = (
        selected_room is not None and private_room_is_blocked(selected_room, current_user)
    )
    current_room_block_reason = None
    if current_room_is_blocked and selected_room_other_user is not None:
        if selected_room_other_user.id in blocked_ids:
            current_room_block_reason = f"You blocked {selected_room_other_user.username}."
        elif selected_room_other_user.id in blocked_by_ids:
            current_room_block_reason = f"{selected_room_other_user.username} has blocked you."

    return {
        "delete_room_form": delete_room_form,
        "room_form": room_form,
        "private_room_form": private_room_form,
        "room_invite_form": room_invite_form,
        "private_chat_form": private_chat_form,
        "public_rooms": public_rooms,
        "private_rooms": private_rooms,
        "all_users": all_users,
        "current_room": selected_room,
        "initial_messages": initial_messages,
        "online_user_ids": list(get_online_user_ids()),
        "blocked_ids": blocked_ids,
        "blocked_by_ids": blocked_by_ids,
        "selected_room_other_user": selected_room_other_user,
        "removable_members": (
            [
                member.user
                for member in selected_room.members
                if member.user_id != current_user.id
            ]
            if can_manage_private_room(selected_room, current_user)
            else []
        ),
        "can_delete_current_room": room_can_be_deleted_by_user(selected_room, current_user),
        "can_invite_to_current_room": can_manage_private_room(selected_room, current_user),
        "can_manage_current_room": can_manage_private_room(selected_room, current_user),
        "current_room_is_blocked": current_room_is_blocked,
        "current_room_block_reason": current_room_block_reason,
    }


def assign_role_if_configured(user):
    if user.email in current_app.config["SUPER_ADMIN_EMAILS"]:
        user.set_role("super_admin")
        return
    if user.email in current_app.config["ADMIN_EMAILS"]:
        user.set_role("admin")


def dispatch_verification_email(user):
    code = user.generate_email_verification_code()
    db.session.commit()
    send_verification_email(user, code)


def log_account_activity(
    action,
    target_type=None,
    target_id=None,
    details=None,
    actor_user=None,
    actor_role=None,
):
    resolved_actor = actor_user
    if resolved_actor is None and current_user.is_authenticated:
        resolved_actor = current_user
    resolved_role = actor_role or (resolved_actor.role if resolved_actor else "anonymous")
    db.session.add(
        AdminAuditLog(
            actor_id=resolved_actor.id if resolved_actor is not None else None,
            actor_role=resolved_role,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )
    )


def build_admin_context(
    user_form=None,
    editing_user=None,
    user_query="",
    room_query="",
    log_query="",
):
    stats = {
        "total_users": db.session.scalar(select(func.count()).select_from(User)) or 0,
        "verified_users": db.session.scalar(
            select(func.count()).select_from(User).where(User.is_email_verified.is_(True))
        ) or 0,
        "admin_users": db.session.scalar(
            select(func.count()).select_from(User).where(User.role == "admin")
        ) or 0,
        "super_admin_users": db.session.scalar(
            select(func.count()).select_from(User).where(User.role == "super_admin")
        ) or 0,
        "total_messages": db.session.scalar(select(func.count()).select_from(Message)) or 0,
        "total_rooms": db.session.scalar(select(func.count()).select_from(Room)) or 0,
    }
    normalized_user_query = (user_query or "").strip()
    normalized_room_query = (room_query or "").strip()
    normalized_log_query = (log_query or "").strip()

    users_statement = select(User).order_by(User.created_at.desc())
    if normalized_user_query:
        user_like = f"%{normalized_user_query}%"
        users_statement = users_statement.where(
            or_(
                User.username.ilike(user_like),
                User.email.ilike(user_like),
                User.role.ilike(user_like),
            )
        )
    users = db.session.scalars(users_statement).all()

    rooms_statement = select(Room).order_by(Room.created_at.desc())
    if normalized_room_query:
        room_like = f"%{normalized_room_query}%"
        rooms_statement = rooms_statement.where(
            Room.name.ilike(room_like)
        )
    rooms = db.session.scalars(rooms_statement).all()

    audit_logs_statement = (
        select(AdminAuditLog)
        .order_by(AdminAuditLog.created_at.desc())
        .limit(100)
    )
    if normalized_log_query:
        log_like = f"%{normalized_log_query}%"
        audit_logs_statement = (
            select(AdminAuditLog)
            .where(
                or_(
                    AdminAuditLog.action.ilike(log_like),
                    AdminAuditLog.actor_role.ilike(log_like),
                    AdminAuditLog.target_type.ilike(log_like),
                    AdminAuditLog.details.ilike(log_like),
                )
            )
            .order_by(AdminAuditLog.created_at.desc())
            .limit(100)
        )
    audit_logs = db.session.scalars(audit_logs_statement).all()
    return {
        "stats": stats,
        "users": users,
        "rooms": rooms,
        "audit_logs": audit_logs,
        "user_query": normalized_user_query,
        "room_query": normalized_room_query,
        "log_query": normalized_log_query,
        "user_form": user_form or AdminUserForm(),
        "editing_user": editing_user,
        "room_delete_form": ActionForm(prefix="admin-room-delete"),
    }


@main_bp.app_context_processor
def inject_notification_context():
    if not current_user.is_authenticated:
        return {}

    return {
        "notification_count": get_unread_notification_count(current_user),
    }


@main_bp.before_app_request
def update_activity_timestamp():
    if not current_user.is_authenticated:
        return

    last_update = session.get("last_seen_sync")
    now = utcnow()
    should_sync = True

    if last_update:
        try:
            previous = datetime.fromisoformat(last_update)
            should_sync = now - previous > timedelta(seconds=60)
        except ValueError:
            should_sync = True

    if should_sync:
        current_user.last_seen = now
        session["last_seen_sync"] = now.isoformat()
        db.session.commit()


@main_bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    return render_template("index.html")


@main_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = RegisterForm()

    if form.validate_on_submit():
        user = User(
            username=form.username.data.strip(),
            email=form.email.data.strip().lower(),
        )
        user.set_password(form.password.data)
        assign_role_if_configured(user)
        db.session.add(user)
        db.session.flush()
        log_account_activity(
            action="account_register",
            target_type="user",
            target_id=user.id,
            details=f"Registered account '{user.username}' with role '{user.role}'.",
            actor_user=user,
            actor_role=user.role,
        )
        db.session.commit()
        dispatch_verification_email(user)
        flash("Account created. Check your email for the 6-digit verification code.", "success")
        return redirect(url_for("main.verify_email", email=user.email))

    return render_template("register.html", form=form)


@main_bp.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    form = VerifyEmailForm()
    prefilled_email = request.args.get("email", "").strip().lower()

    if request.method == "GET" and prefilled_email:
        form.email.data = prefilled_email

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        code = form.code.data.strip()
        user = db.session.scalar(select(User).where(User.email == email))

        if user is None:
            log_account_activity(
                action="account_verify_failed",
                target_type="email",
                details=f"Verification attempt for unknown email '{email}'.",
            )
            db.session.commit()
            flash("No account was found for that email address.", "error")
        elif user.is_email_verified:
            log_account_activity(
                action="account_verify_skipped",
                target_type="user",
                target_id=user.id,
                details=f"Verification attempted but '{user.username}' is already verified.",
                actor_user=user,
                actor_role=user.role,
            )
            db.session.commit()
            flash("That email address is already verified. You can sign in now.", "success")
            return redirect(url_for("main.login"))
        elif not user.verify_email_code(code):
            log_account_activity(
                action="account_verify_failed",
                target_type="user",
                target_id=user.id,
                details=f"Invalid/expired verification code for '{user.username}'.",
                actor_user=user,
                actor_role=user.role,
            )
            db.session.commit()
            flash("That verification code is invalid or has expired.", "error")
        else:
            user.clear_verification_state()
            log_account_activity(
                action="account_verify_success",
                target_type="user",
                target_id=user.id,
                details=f"Verified account '{user.username}'.",
                actor_user=user,
                actor_role=user.role,
            )
            db.session.commit()
            flash("Your email address has been verified. You can sign in now.", "success")
            return redirect(url_for("main.login"))

    return render_template("verify_email.html", form=form)


@main_bp.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    form = ResendVerificationForm()

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = db.session.scalar(
            select(User).where(User.email == email)
        )

        if user is None:
            log_account_activity(
                action="account_resend_verification_failed",
                target_type="email",
                details=f"Resend verification requested for unknown email '{email}'.",
            )
            db.session.commit()
            flash("No account was found for that email address.", "error")
        elif user.is_email_verified:
            log_account_activity(
                action="account_resend_verification_skipped",
                target_type="user",
                target_id=user.id,
                details=f"Resend requested for already verified account '{user.username}'.",
                actor_user=user,
                actor_role=user.role,
            )
            db.session.commit()
            flash("That email address is already verified.", "success")
            return redirect(url_for("main.login"))
        else:
            log_account_activity(
                action="account_resend_verification",
                target_type="user",
                target_id=user.id,
                details=f"Resent verification code to '{user.username}'.",
                actor_user=user,
                actor_role=user.role,
            )
            dispatch_verification_email(user)
            flash("A fresh 6-digit verification code has been sent.", "success")
            return redirect(url_for("main.verify_email", email=user.email))

    return render_template("resend_verification.html", form=form)


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    show_resend_verification = False

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = db.session.scalar(
            select(User).where(User.email == email)
        )

        if user is None or not user.check_password(form.password.data):
            log_account_activity(
                action="account_login_failed",
                target_type="email",
                details=f"Failed login attempt for '{email}'.",
            )
            db.session.commit()
            flash("Invalid email or password.", "error")
        elif not user.is_email_verified:
            log_account_activity(
                action="account_login_blocked_unverified",
                target_type="user",
                target_id=user.id,
                details=f"Blocked login for unverified account '{user.username}'.",
                actor_user=user,
                actor_role=user.role,
            )
            db.session.commit()
            flash("Verify your email address with the 6-digit code before signing in.", "error")
            show_resend_verification = True
        else:
            login_user(user)
            user.touch_last_seen()
            log_account_activity(
                action="account_login",
                target_type="user",
                target_id=user.id,
                details=f"Successful login for '{user.username}'.",
                actor_user=user,
                actor_role=user.role,
            )
            db.session.commit()
            flash("Welcome back.", "success")
            next_url = request.args.get("next")
            return redirect(next_url or url_for("main.dashboard"))

    return render_template(
        "login.html",
        form=form,
        show_resend_verification=show_resend_verification,
    )


@main_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    current_user.touch_last_seen()
    log_account_activity(
        action="account_logout",
        target_type="user",
        target_id=current_user.id,
        details=f"User '{current_user.username}' logged out.",
    )
    db.session.commit()
    logout_user()
    session.pop("last_seen_sync", None)
    flash("You have been signed out.", "success")
    return redirect(url_for("main.login"))


@main_bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", **build_dashboard_context())


@main_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = UserProfileForm(obj=current_user)
    if form.validate_on_submit():
        avatar_file = request.files.get("avatar_file")
        uploaded_avatar_url = None
        if avatar_file is not None and avatar_file.filename:
            try:
                uploaded_avatar_url = save_uploaded_profile_avatar(avatar_file)
            except ValueError as exc:
                form.avatar_url.errors.append(str(exc))
                return render_template(
                    "profile.html",
                    profile_user=current_user,
                    form=form,
                    can_edit=True,
                ), 400

        previous_avatar_url = current_user.avatar_url
        previous_display_name = current_user.display_name
        previous_bio = current_user.bio
        current_user.display_name = (form.display_name.data or "").strip() or None
        current_user.bio = (form.bio.data or "").strip() or None
        current_user.avatar_url = (
            uploaded_avatar_url if uploaded_avatar_url is not None
            else (form.avatar_url.data or "").strip() or None
        )
        changed_fields = []
        if previous_display_name != current_user.display_name:
            changed_fields.append("display_name")
        if previous_bio != current_user.bio:
            changed_fields.append("bio")
        if previous_avatar_url != current_user.avatar_url:
            changed_fields.append("avatar_url")
        log_account_activity(
            action="account_profile_updated",
            target_type="user",
            target_id=current_user.id,
            details=(
                f"Updated profile fields: {', '.join(changed_fields)}."
                if changed_fields else
                "Profile saved with no visible field changes."
            ),
        )
        db.session.commit()
        if (
            uploaded_avatar_url is not None
            and previous_avatar_url
            and previous_avatar_url != uploaded_avatar_url
        ):
            delete_local_profile_avatar_if_any(previous_avatar_url)
        socketio.emit("user_profile_updated", serialize_public_profile(current_user))
        flash("Profile updated.", "success")
        return redirect(url_for("main.profile"))

    return render_template(
        "profile.html",
        profile_user=current_user,
        form=form,
        can_edit=True,
    )


@main_bp.route("/users/<int:user_id>/profile")
@login_required
def view_profile(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    return render_template(
        "profile.html",
        profile_user=user,
        form=None,
        can_edit=False,
    )


@main_bp.route("/rooms/<int:room_id>")
@login_required
def view_room(room_id):
    room = db.session.get(Room, room_id)
    if room is None:
        flash("Room was deleted.", "error")
        return redirect(url_for("main.dashboard"))
    return render_template("dashboard.html", **build_dashboard_context(room_id))


@main_bp.route("/rooms/<int:room_id>/media", methods=["POST"])
@login_required
def upload_room_media(room_id):
    room = get_accessible_room(room_id, current_user)
    if room is None:
        return jsonify({"message": "You do not have access to that room."}), 403

    media_file = request.files.get("media")
    if media_file is None or not media_file.filename:
        return jsonify({"message": "Choose an image or video to upload."}), 400

    try:
        message_type, media_url = save_uploaded_media(media_file)
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400

    message = Message(
        user=current_user,
        room=room,
        content=media_url,
        message_type=message_type,
    )
    db.session.add(message)
    db.session.flush()
    db.session.add(MessageRead(message=message, user=current_user))
    current_user.touch_last_seen()
    db.session.commit()
    db.session.refresh(message)

    payload = serialize_message_payload(message, current_user.id)
    socketio.emit("receive_message", payload, to=room.socket_room)
    for member in room.members:
        if member.user_id == current_user.id:
            continue
        emit_notification_item_for_user(
            member.user,
            serialize_notification_message_for_user(message, member.user),
        )
        emit_notification_refresh_for_user(member.user_id)
    return jsonify({"message": payload}), 201


@main_bp.route("/notifications")
@login_required
def notifications():
    notification_items = build_notification_items(current_user)
    return render_template(
        "notifications.html",
        notifications=notification_items,
        accept_invite_form=ActionForm(prefix="accept-invite"),
        decline_invite_form=ActionForm(prefix="decline-invite"),
    )


@main_bp.route("/rooms", methods=["POST"])
@admin_required
def create_room():
    form = RoomForm(prefix="room")
    private_chat_form = PrivateChatForm(prefix="private")

    if form.validate_on_submit():
        room_name = form.name.data.strip()
        if room_name.lower().startswith("dm:"):
            form.name.errors.append("Private room name cannot start with 'dm:'.")
            context = build_dashboard_context()
            context["private_room_form"] = form
            return render_template("dashboard.html", **context), 400
        existing_room = db.session.scalar(select(Room).where(Room.name == room_name))

        if existing_room:
            flash("A room with that name already exists.", "error")
            return redirect(url_for("main.view_room", room_id=existing_room.id))

        room = Room(name=room_name, is_private=False)
        room.add_member(current_user)
        db.session.add(room)
        db.session.commit()
        flash(f"Room '{room_name}' created.", "success")
        return redirect(url_for("main.view_room", room_id=room.id))

    context = build_dashboard_context()
    context["room_form"] = form
    context["private_chat_form"] = private_chat_form
    return render_template("dashboard.html", **context), 400


@main_bp.route("/private-chats", methods=["POST"])
@login_required
def create_private_chat():
    room_form = RoomForm(prefix="room")
    form = PrivateChatForm(prefix="private")

    if form.validate_on_submit():
        target_username = form.username.data.strip()
        target_user = db.session.scalar(
            select(User).where(User.username == target_username, User.id != current_user.id)
        )

        if target_user is None:
            form.username.errors.append("That user does not exist.")
        elif users_are_blocked(current_user.id, target_user.id):
            form.username.errors.append("That conversation is unavailable because one of you has blocked the other.")
        else:
            room = get_or_create_private_room(current_user, target_user)
            room.add_member(current_user)
            room.add_member(target_user)
            db.session.add(room)
            db.session.commit()
            return redirect(url_for("main.view_room", room_id=room.id))

    context = build_dashboard_context()
    context["room_form"] = room_form
    context["private_chat_form"] = form
    return render_template("dashboard.html", **context), 400


@main_bp.route("/rooms/private", methods=["POST"])
@login_required
def create_private_room():
    form = PrivateRoomForm(prefix="private-room")
    if form.validate_on_submit():
        room_name = form.name.data.strip()
        existing_room = db.session.scalar(select(Room).where(Room.name == room_name))
        if existing_room:
            flash("A room with that name already exists.", "error")
            return redirect(url_for("main.view_room", room_id=existing_room.id))

        room = Room(name=room_name, is_private=True)
        room.owner_id = current_user.id
        room.add_member(current_user)
        db.session.add(room)
        db.session.commit()
        flash(f"Private room '{room_name}' created. Invite people to join.", "success")
        return redirect(url_for("main.view_room", room_id=room.id))

    context = build_dashboard_context()
    context["private_room_form"] = form
    return render_template("dashboard.html", **context), 400


@main_bp.route("/rooms/<int:room_id>/invite", methods=["POST"])
@login_required
def invite_to_private_room(room_id):
    form = RoomInviteForm(prefix="invite-room")
    room = db.session.get(Room, room_id)
    if room is None:
        abort(404)
    if not can_manage_private_room(room, current_user):
        abort(403)
    if not form.validate_on_submit():
        flash("Provide a valid username to invite.", "error")
        return redirect(url_for("main.view_room", room_id=room.id))

    username = form.username.data.strip()
    invitee = db.session.scalar(
        select(User).where(
            User.username == username,
            User.id != current_user.id,
        )
    )
    if invitee is None:
        flash("That user does not exist.", "error")
        return redirect(url_for("main.view_room", room_id=room.id))
    if room.has_member(invitee):
        flash(f"{invitee.username} is already a member of this room.", "error")
        return redirect(url_for("main.view_room", room_id=room.id))
    if users_are_blocked(current_user.id, invitee.id):
        flash("You cannot invite this user because one of you has blocked the other.", "error")
        return redirect(url_for("main.view_room", room_id=room.id))

    existing_invitation = db.session.scalar(
        select(RoomInvitation).where(
            RoomInvitation.room_id == room.id,
            RoomInvitation.invitee_id == invitee.id,
        )
    )
    if existing_invitation is not None:
        if existing_invitation.status == "pending":
            flash(f"{invitee.username} already has a pending invitation.", "error")
            return redirect(url_for("main.view_room", room_id=room.id))

        existing_invitation.inviter_id = current_user.id
        existing_invitation.status = "pending"
        existing_invitation.responded_at = None
        existing_invitation.created_at = utcnow()
        invitation = existing_invitation
    else:
        invitation = RoomInvitation(
            room_id=room.id,
            inviter_id=current_user.id,
            invitee_id=invitee.id,
            status="pending",
        )
        db.session.add(invitation)
    db.session.commit()
    db.session.refresh(invitation)

    emit_notification_item_for_user(
        invitee,
        serialize_room_invitation_notification(invitation, invitee),
    )
    emit_notification_refresh_for_user(invitee.id)
    flash(f"Invitation sent to {invitee.username}.", "success")
    return redirect(url_for("main.view_room", room_id=room.id))


@main_bp.route("/rooms/<int:room_id>/members/<int:user_id>/remove", methods=["POST"])
@login_required
def remove_private_room_member(room_id, user_id):
    room = db.session.get(Room, room_id)
    if room is None:
        abort(404)
    if not can_manage_private_room(room, current_user):
        abort(403)
    if user_id == current_user.id:
        flash("Room owners cannot remove themselves.", "error")
        return redirect(url_for("main.view_room", room_id=room_id))

    member_user = db.session.get(User, user_id)
    if member_user is None or not room.has_member(member_user):
        flash("That user is not a member of this room.", "error")
        return redirect(url_for("main.view_room", room_id=room_id))

    room.remove_member(member_user)
    db.session.commit()
    socketio.emit("room_members_updated", serialize_room_members_payload(room), to=room.socket_room)
    flash(f"{member_user.username} was removed from this private room.", "success")
    return redirect(url_for("main.view_room", room_id=room_id))


@main_bp.route("/invitations/<int:invitation_id>/accept", methods=["POST"])
@login_required
def accept_room_invitation(invitation_id):
    form = ActionForm(prefix="accept-invite")
    invitation = db.session.get(RoomInvitation, invitation_id)
    if invitation is None or invitation.invitee_id != current_user.id:
        abort(404)
    if not form.validate_on_submit():
        flash("Unable to verify the invitation action.", "error")
        return redirect(url_for("main.notifications"))
    if invitation.status != "pending":
        flash("That invitation is no longer pending.", "error")
        return redirect(url_for("main.notifications"))

    room = invitation.room
    if room is None:
        flash("That room is no longer available.", "error")
        return redirect(url_for("main.notifications"))

    if not room.has_member(current_user):
        room.add_member(current_user)

    invitation.status = "accepted"
    invitation.responded_at = utcnow()
    db.session.commit()
    socketio.emit("room_members_updated", serialize_room_members_payload(room), to=room.socket_room)
    emit_notification_refresh_for_user(current_user.id)
    emit_notification_refresh_for_user(invitation.inviter_id)
    flash(f"You joined {room.display_name_for(current_user)}.", "success")
    return redirect(url_for("main.view_room", room_id=room.id))


@main_bp.route("/invitations/<int:invitation_id>/decline", methods=["POST"])
@login_required
def decline_room_invitation(invitation_id):
    form = ActionForm(prefix="decline-invite")
    invitation = db.session.get(RoomInvitation, invitation_id)
    if invitation is None or invitation.invitee_id != current_user.id:
        abort(404)
    if not form.validate_on_submit():
        flash("Unable to verify the invitation action.", "error")
        return redirect(url_for("main.notifications"))
    if invitation.status != "pending":
        flash("That invitation is no longer pending.", "error")
        return redirect(url_for("main.notifications"))

    invitation.status = "declined"
    invitation.responded_at = utcnow()
    db.session.commit()
    emit_notification_refresh_for_user(current_user.id)
    emit_notification_refresh_for_user(invitation.inviter_id)
    flash("Invitation declined.", "success")
    return redirect(url_for("main.notifications"))


@main_bp.route("/rooms/<int:room_id>/delete", methods=["POST"])
@login_required
def delete_room(room_id):
    form = ActionForm(prefix="delete-room")
    room = db.session.get(Room, room_id)
    if room is None:
        abort(404)

    if not form.validate_on_submit():
        flash("Unable to verify the delete request.", "error")
        return redirect(url_for("main.view_room", room_id=room_id))

    if not room_can_be_deleted_by_user(room, current_user):
        flash("That room cannot be deleted.", "error")
        return redirect(url_for("main.view_room", room_id=room_id))

    room_name = room.name
    invitations = db.session.scalars(
        select(RoomInvitation).where(RoomInvitation.room_id == room.id)
    ).all()
    for invitation in invitations:
        db.session.delete(invitation)
    db.session.delete(room)
    db.session.commit()
    flash(f"Room '{room_name}' deleted.", "success")
    return redirect(url_for("main.dashboard"))


@main_bp.route("/admin")
@admin_required
def admin_dashboard():
    return render_template(
        "admin_dashboard.html",
        **build_admin_context(
            user_query=request.args.get("user_query", ""),
            room_query=request.args.get("room_query", ""),
            log_query=request.args.get("log_query", ""),
        ),
    )


@main_bp.route("/admin/users/<int:user_id>/logs")
@admin_required
def admin_user_logs(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    return redirect(url_for("main.admin_dashboard"))


@main_bp.route("/admin/users/<int:user_id>/logs.json")
@admin_required
def admin_user_logs_json(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    logs = db.session.scalars(
        select(AdminAuditLog)
        .where(
            (AdminAuditLog.actor_id == user.id)
            | (
                (AdminAuditLog.target_type == "user")
                & (AdminAuditLog.target_id == user.id)
            )
        )
        .order_by(AdminAuditLog.created_at.desc())
        .limit(100)
    ).all()

    return jsonify(
        {
            "user": {
                "id": user.id,
                "username": user.username,
            },
            "logs": [
                {
                    "created_at": log.created_at.isoformat(),
                    "actor": log.actor.username if log.actor else "Deleted user",
                    "actor_role": log.actor_role,
                    "action": log.action,
                    "target": (
                        f"{log.target_type} #{log.target_id}"
                        if log.target_type and log.target_id
                        else "—"
                    ),
                    "details": log.details or "—",
                }
                for log in logs
            ],
        }
    )


@main_bp.route("/admin/users/new", methods=["POST"])
@admin_required
def admin_create_user():
    form = AdminUserForm()
    if not form.password.data:
        form.password.errors.append("Password is required when creating a user.")
        return render_template("admin_dashboard.html", **build_admin_context(user_form=form)), 400

    if form.validate_on_submit():
        user = User(
            username=form.username.data.strip(),
            email=form.email.data.strip().lower(),
        )
        user.set_password(form.password.data or "changeme123")
        user.set_role(form.role.data)
        user.is_email_verified = form.is_email_verified.data == "true"
        if user.is_email_verified:
            user.clear_verification_state()

        db.session.add(user)
        db.session.flush()
        log_account_activity(
            action="admin_create_user",
            target_type="user",
            target_id=user.id,
            details=f"Created user '{user.username}' with role '{user.role}'.",
        )
        db.session.commit()
        if not user.is_email_verified:
            dispatch_verification_email(user)
        flash("User created successfully.", "success")
        return redirect(url_for("main.admin_dashboard"))

    return render_template("admin_dashboard.html", **build_admin_context(user_form=form)), 400


@main_bp.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    form = AdminUserForm(original_user=user, obj=user)
    if request.method == "GET":
        form.role.data = user.role
        form.is_email_verified.data = "true" if user.is_email_verified else "false"

    if form.validate_on_submit():
        previous_role = user.role
        previous_email_verified = user.is_email_verified
        user.username = form.username.data.strip()
        user.email = form.email.data.strip().lower()
        user.set_role(form.role.data)
        if form.password.data:
            user.set_password(form.password.data)

        if form.is_email_verified.data == "true":
            user.is_email_verified = True
            user.clear_verification_state()
            log_account_activity(
                action="admin_edit_user",
                target_type="user",
                target_id=user.id,
                details=(
                    f"Updated user '{user.username}' role '{previous_role}' -> '{user.role}', "
                    f"verified '{previous_email_verified}' -> '{user.is_email_verified}'."
                ),
            )
            db.session.commit()
        else:
            user.is_email_verified = False
            log_account_activity(
                action="admin_edit_user",
                target_type="user",
                target_id=user.id,
                details=(
                    f"Updated user '{user.username}' role '{previous_role}' -> '{user.role}', "
                    f"verified '{previous_email_verified}' -> '{user.is_email_verified}', and resent verification."
                ),
            )
            dispatch_verification_email(user)
        flash("User updated successfully.", "success")
        return redirect(url_for("main.admin_dashboard"))

    return render_template(
        "admin_dashboard.html",
        **build_admin_context(user_form=form, editing_user=user),
    ), 400


@main_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    if user.id == current_user.id:
        flash("You cannot delete your own account from the admin dashboard.", "error")
        return redirect(url_for("main.admin_dashboard"))

    log_account_activity(
        action="admin_delete_user",
        target_type="user",
        target_id=user.id,
        details=f"Deleted user '{user.username}' ({user.email}) with role '{user.role}'.",
    )
    db.session.delete(user)
    db.session.commit()
    flash("User deleted successfully.", "success")
    return redirect(url_for("main.admin_dashboard"))


@main_bp.route("/admin/rooms/<int:room_id>/delete", methods=["POST"])
@admin_required
def admin_delete_room(room_id):
    form = ActionForm(prefix="admin-room-delete")
    room = db.session.get(Room, room_id)
    if room is None:
        abort(404)

    if not form.validate_on_submit():
        flash("Unable to verify the room delete request.", "error")
        return redirect(url_for("main.admin_dashboard"))

    invitations = db.session.scalars(
        select(RoomInvitation).where(RoomInvitation.room_id == room.id)
    ).all()
    for invitation in invitations:
        db.session.delete(invitation)

    room_name = room.name
    log_account_activity(
        action="admin_delete_room",
        target_type="room",
        target_id=room.id,
        details=f"Deleted room '{room_name}' (private={room.is_private}).",
    )
    db.session.delete(room)
    db.session.commit()
    flash(f"Room '{room_name}' deleted.", "success")
    return redirect(url_for("main.admin_dashboard"))


@main_bp.route("/users/<int:user_id>/block", methods=["POST"])
@login_required
def block_user(user_id):
    target_user = db.session.get(User, user_id)
    if target_user is None:
        abort(404)

    if target_user.id == current_user.id:
        flash("You cannot block your own account.", "error")
        return redirect(request.referrer or url_for("main.dashboard"))

    existing_block = db.session.scalar(
        select(UserBlock).where(
            UserBlock.blocker_id == current_user.id,
            UserBlock.blocked_id == target_user.id,
        )
    )
    if existing_block is None:
        db.session.add(UserBlock(blocker_id=current_user.id, blocked_id=target_user.id))
        log_account_activity(
            action="account_block_user",
            target_type="user",
            target_id=target_user.id,
            details=f"'{current_user.username}' blocked '{target_user.username}'.",
        )
        db.session.commit()

    flash(f"{target_user.username} has been blocked.", "success")
    return redirect(request.referrer or url_for("main.dashboard"))


@main_bp.route("/users/<int:user_id>/unblock", methods=["POST"])
@login_required
def unblock_user(user_id):
    target_user = db.session.get(User, user_id)
    if target_user is None:
        abort(404)

    block = db.session.scalar(
        select(UserBlock).where(
            UserBlock.blocker_id == current_user.id,
            UserBlock.blocked_id == target_user.id,
        )
    )
    if block is not None:
        db.session.delete(block)
        log_account_activity(
            action="account_unblock_user",
            target_type="user",
            target_id=target_user.id,
            details=f"'{current_user.username}' unblocked '{target_user.username}'.",
        )
        db.session.commit()

    flash(f"{target_user.username} has been unblocked.", "success")
    return redirect(request.referrer or url_for("main.dashboard"))
