import os
from datetime import datetime, timedelta
from functools import wraps
from uuid import uuid4

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.utils import secure_filename

from app import repository as repo
from app import socketio
from app.forms import (
    ActionForm,
    AdminUserForm,
    ForgotPasswordForm,
    LoginForm,
    PrivateChatForm,
    UserProfileForm,
    PrivateRoomForm,
    RegisterForm,
    ResendVerificationForm,
    ResetPasswordForm,
    RoomInviteForm,
    RoomForm,
    VerifyEmailForm,
)
from app.mail import EmailDeliveryError, send_password_reset_email, send_verification_email
from app.models import (
    AdminAuditLog,
    Message,
    MessageRead,
    Room,
    RoomInvitation,
    User,
    UserBlock,
    get_or_create_private_room,
    users_are_blocked,
    utcnow,
)
from app.sockets import (
    emit_to_user,
    get_accessible_room,
    get_online_user_ids,
    serialize_message_payload,
    serialize_notification_message_for_user,
)


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
    row = repo.get_room_by_name("General")
    if row is None:
        room = Room(name="General", is_private=False)
        room.save()
        return room
    return Room.from_row(row)


def ensure_room_membership(user, room, commit=False):
    if not room.has_member(user):
        room.add_member(user)


def mark_room_messages_as_read(room, user):
    newly_read_ids = []

    if not room.messages:
        room.load_messages()

    for message in room.messages:
        if message.user_id == user.id or message.is_read_by(user.id):
            continue

        MessageRead(message=message, user=user).save()
        newly_read_ids.append(message.id)

    return newly_read_ids


def build_room_lists(user):
    public_rooms = [Room.from_row(row) for row in repo.list_public_rooms()]
    private_rooms = []

    for row in repo.list_private_rooms_for_user(user.id):
        room = Room.from_row(row)
        room.load_members()
        if room.is_direct_message:
            other_member = room.other_member_for(user)
            if other_member and not users_are_blocked(user.id, other_member.id):
                private_rooms.append(room)
            continue
        private_rooms.append(room)

    return public_rooms, private_rooms


def build_block_maps(user):
    blocked_ids = {
        block["blocked_id"] for block in repo.list_blocks_initiated_by(user.id)
    }
    blocked_by_ids = {
        block["blocker_id"] for block in repo.list_blocks_received_by(user.id)
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
    if not room.members:
        room.load_members()
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
    rows = repo.fetch_unread_message_notifications(user.id, limit=limit)
    notifications = []

    for row in rows:
        room = Room.get(row["room_id"], with_members=True)
        if room is None:
            continue
        if room.is_private and private_room_is_blocked(room, user):
            continue

        notifications.append(
            {
                "message_id": row["id"],
                "kind": "message",
                "room_id": room.id,
                "room_name": room.display_name_for(user),
                "sender_username": row["sender_username"],
                "preview": row["content"],
                "created_at": row["created_at"],
                "is_private": room.is_private,
            }
        )

    return notifications


def build_mention_notifications(user, limit=50):
    rows = repo.fetch_unread_mention_notifications(user.id, limit=limit)
    notifications = []

    for row in rows:
        room = Room.get(row["room_id"], with_members=True)
        if room is None:
            continue
        if room.is_private and private_room_is_blocked(room, user):
            continue

        notifications.append(
            {
                "mention_id": row["mention_id"],
                "kind": "mention",
                "message_id": row["message_id"],
                "room_id": room.id,
                "room_name": room.display_name_for(user),
                "sender_username": row["sender_username"],
                "preview": f"You were mentioned by {row['sender_username']}: {row['content']}",
                "created_at": row["mention_created_at"],
                "is_private": room.is_private,
            }
        )

    return notifications


def build_room_invitation_notifications(user, limit=50):
    rows = repo.fetch_pending_invitation_notifications(user.id, limit=limit)
    notifications = []

    for row in rows:
        invitation = RoomInvitation.from_row(row)
        room = Room.get(row["room_id"], with_members=True)
        inviter = User.from_row(
            {"id": row["inviter_id"], "username": row["inviter_username"]}
        )
        invitation.room = room
        invitation.inviter = inviter
        notifications.append(serialize_room_invitation_notification(invitation, user))

    return notifications


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
    user = User.from_row(repo.get_user_by_id(user_id))
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
    all_users = [
        User.from_row(row) for row in repo.list_users_excluding(current_user.id)
    ]

    selected_room = None
    initial_messages = []

    if selected_room_id is not None:
        selected_room = Room.get(
            selected_room_id,
            with_members=True,
            with_messages=True,
        )
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
        selected_room.load_messages()
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
    user.save()
    send_verification_email(user, code)


def dispatch_password_reset_email(user):
    code = user.generate_password_reset_code()
    user.save()
    send_password_reset_email(user, code)


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
    AdminAuditLog(
        actor_id=resolved_actor.id if resolved_actor is not None else None,
        actor_role=resolved_role,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
    ).save()


def build_admin_context(
    user_form=None,
    editing_user=None,
    user_query="",
    room_query="",
    log_query="",
):
    stats = repo.get_admin_dashboard_stats()
    normalized_user_query = (user_query or "").strip()
    normalized_room_query = (room_query or "").strip()
    normalized_log_query = (log_query or "").strip()

    if normalized_user_query:
        users = [User.from_row(row) for row in repo.search_users_admin(normalized_user_query)]
    else:
        users = [User.from_row(row) for row in repo.list_all_users_admin()]

    if normalized_room_query:
        rooms = [Room.from_row(row) for row in repo.search_rooms_admin(normalized_room_query)]
    else:
        rooms = [Room.from_row(row) for row in repo.list_all_rooms_admin()]

    audit_logs = [
        AdminAuditLog.from_row(row)
        for row in repo.list_admin_audit_logs(
            normalized_log_query or None,
            limit=100,
        )
    ]

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
        current_user.save()


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
        user.save()
        log_account_activity(
            action="account_register",
            target_type="user",
            target_id=user.id,
            details=f"Registered account '{user.username}' with role '{user.role}'.",
            actor_user=user,
            actor_role=user.role,
        )
        try:
            dispatch_verification_email(user)
        except EmailDeliveryError as exc:
            current_app.logger.exception("Verification email failed for %s", user.email)
            flash(
                f"Account created, but the verification email could not be sent. {exc} "
                "Use Resend verification after fixing .env, or set MAIL_SUPPRESS_SEND=true "
                "and check the server console for the code.",
                "warning",
            )
            return redirect(url_for("main.resend_verification", email=user.email))
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
        user = User.from_row(repo.get_user_by_email(email))

        if user is None:
            log_account_activity(
                action="account_verify_failed",
                target_type="email",
                details=f"Verification attempt for unknown email '{email}'.",
            )
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
            flash("That verification code is invalid or has expired.", "error")
        else:
            user.clear_verification_state()
            user.save()
            log_account_activity(
                action="account_verify_success",
                target_type="user",
                target_id=user.id,
                details=f"Verified account '{user.username}'.",
                actor_user=user,
                actor_role=user.role,
            )
            flash("Your email address has been verified. You can sign in now.", "success")
            return redirect(url_for("main.login"))

    return render_template("verify_email.html", form=form)


@main_bp.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    form = ResendVerificationForm()
    prefilled_email = request.args.get("email", "").strip().lower()
    if request.method == "GET" and prefilled_email:
        form.email.data = prefilled_email

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.from_row(repo.get_user_by_email(email))

        if user is None:
            log_account_activity(
                action="account_resend_verification_failed",
                target_type="email",
                details=f"Resend verification requested for unknown email '{email}'.",
            )
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
            flash("That email address is already verified.", "success")
            return redirect(url_for("main.login"))
        else:
            try:
                dispatch_verification_email(user)
            except EmailDeliveryError as exc:
                current_app.logger.exception("Verification email failed for %s", user.email)
                flash(
                    f"Could not send email. {exc} "
                    "Set MAIL_SUPPRESS_SEND=true and check the server console for the code.",
                    "error",
                )
                return redirect(url_for("main.resend_verification", email=user.email))
            log_account_activity(
                action="account_resend_verification",
                target_type="user",
                target_id=user.id,
                details=f"Resent verification code to '{user.username}'.",
                actor_user=user,
                actor_role=user.role,
            )
            flash("A fresh 6-digit verification code has been sent.", "success")
            return redirect(url_for("main.verify_email", email=user.email))

    return render_template("resend_verification.html", form=form)


@main_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = ForgotPasswordForm()
    prefilled_email = request.args.get("email", "").strip().lower()
    if request.method == "GET" and prefilled_email:
        form.email.data = prefilled_email

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.from_row(repo.get_user_by_email(email))

        if user is not None:
            try:
                dispatch_password_reset_email(user)
            except EmailDeliveryError as exc:
                current_app.logger.exception("Password reset email failed for %s", user.email)
                flash(
                    f"Could not send email. {exc} "
                    "Set MAIL_SUPPRESS_SEND=true and check the server console for the code.",
                    "error",
                )
                return redirect(url_for("main.reset_password", email=user.email))
            log_account_activity(
                action="account_password_reset_requested",
                target_type="user",
                target_id=user.id,
                details=f"Password reset code sent to '{user.username}'.",
                actor_user=user,
                actor_role=user.role,
            )

        flash(
            "If an account exists for that email, a 6-digit reset code has been sent.",
            "success",
        )
        return redirect(url_for("main.reset_password", email=email))

    return render_template("forgot_password.html", form=form)


@main_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = ResetPasswordForm()
    prefilled_email = request.args.get("email", "").strip().lower()
    if request.method == "GET" and prefilled_email:
        form.email.data = prefilled_email

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        code = form.code.data.strip()
        user = User.from_row(repo.get_user_by_email(email))

        if user is None:
            log_account_activity(
                action="account_password_reset_failed",
                target_type="email",
                details=f"Password reset attempt for unknown email '{email}'.",
            )
            flash("That reset code is invalid or has expired.", "error")
        elif not user.verify_password_reset_code(code):
            log_account_activity(
                action="account_password_reset_failed",
                target_type="user",
                target_id=user.id,
                details=f"Invalid/expired password reset code for '{user.username}'.",
                actor_user=user,
                actor_role=user.role,
            )
            flash("That reset code is invalid or has expired.", "error")
        else:
            user.set_password(form.password.data)
            user.clear_password_reset_state()
            user.save()
            log_account_activity(
                action="account_password_reset_success",
                target_type="user",
                target_id=user.id,
                details=f"Password reset completed for '{user.username}'.",
                actor_user=user,
                actor_role=user.role,
            )
            flash("Your password has been reset. You can sign in now.", "success")
            return redirect(url_for("main.login"))

    return render_template("reset_password.html", form=form)


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    show_resend_verification = False

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.from_row(repo.get_user_by_email(email))

        if user is None or not user.check_password(form.password.data):
            log_account_activity(
                action="account_login_failed",
                target_type="email",
                details=f"Failed login attempt for '{email}'.",
            )
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
        current_user.save()
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
    user = User.from_row(repo.get_user_by_id(user_id))
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
    room = Room.get(room_id)
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
    message.save()
    MessageRead(message=message, user=current_user).save()
    current_user.touch_last_seen()
    message.reload()

    payload = serialize_message_payload(message, current_user.id)
    socketio.emit("receive_message", payload, to=room.socket_room)
    if not room.members:
        room.load_members()
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
        existing_row = repo.get_room_by_name(room_name)

        if existing_row:
            flash("A room with that name already exists.", "error")
            return redirect(url_for("main.view_room", room_id=existing_row["id"]))

        room = Room(name=room_name, is_private=False)
        room.save()
        room.add_member(current_user)
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
        target_row = repo.get_user_by_username(target_username)

        if target_row is None or target_row["id"] == current_user.id:
            form.username.errors.append("That user does not exist.")
        elif users_are_blocked(current_user.id, target_row["id"]):
            form.username.errors.append(
                "That conversation is unavailable because one of you has blocked the other."
            )
        else:
            target_user = User.from_row(target_row)
            room = get_or_create_private_room(current_user, target_user)
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
        existing_row = repo.get_room_by_name(room_name)
        if existing_row:
            flash("A room with that name already exists.", "error")
            return redirect(url_for("main.view_room", room_id=existing_row["id"]))

        room = Room(name=room_name, is_private=True)
        room.owner_id = current_user.id
        room.save()
        room.add_member(current_user)
        flash(f"Private room '{room_name}' created. Invite people to join.", "success")
        return redirect(url_for("main.view_room", room_id=room.id))

    context = build_dashboard_context()
    context["private_room_form"] = form
    return render_template("dashboard.html", **context), 400


@main_bp.route("/rooms/<int:room_id>/invite", methods=["POST"])
@login_required
def invite_to_private_room(room_id):
    form = RoomInviteForm(prefix="invite-room")
    room = Room.get(room_id, with_members=True)
    if room is None:
        abort(404)
    if not can_manage_private_room(room, current_user):
        abort(403)
    if not form.validate_on_submit():
        flash("Provide a valid username to invite.", "error")
        return redirect(url_for("main.view_room", room_id=room.id))

    username = form.username.data.strip()
    invitee_row = repo.get_user_by_username(username)
    invitee = (
        User.from_row(invitee_row)
        if invitee_row is not None and invitee_row["id"] != current_user.id
        else None
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

    existing_row = repo.get_room_invitation_by_room_and_invitee(room.id, invitee.id)
    if existing_row is not None:
        invitation = RoomInvitation.from_row(existing_row)
        if invitation.status == "pending":
            flash(f"{invitee.username} already has a pending invitation.", "error")
            return redirect(url_for("main.view_room", room_id=room.id))

        invitation.inviter_id = current_user.id
        invitation.status = "pending"
        invitation.responded_at = None
        invitation.created_at = utcnow()
        invitation.save()
    else:
        invitation = RoomInvitation(
            room_id=room.id,
            inviter_id=current_user.id,
            invitee_id=invitee.id,
            status="pending",
        )
        invitation.save()

    invitation.reload()

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
    room = Room.get(room_id, with_members=True)
    if room is None:
        abort(404)
    if not can_manage_private_room(room, current_user):
        abort(403)
    if user_id == current_user.id:
        flash("Room owners cannot remove themselves.", "error")
        return redirect(url_for("main.view_room", room_id=room_id))

    member_user = User.from_row(repo.get_user_by_id(user_id))
    if member_user is None or not room.has_member(member_user):
        flash("That user is not a member of this room.", "error")
        return redirect(url_for("main.view_room", room_id=room_id))

    room.remove_member(member_user)
    socketio.emit("room_members_updated", serialize_room_members_payload(room), to=room.socket_room)
    flash(f"{member_user.username} was removed from this private room.", "success")
    return redirect(url_for("main.view_room", room_id=room_id))


@main_bp.route("/invitations/<int:invitation_id>/accept", methods=["POST"])
@login_required
def accept_room_invitation(invitation_id):
    form = ActionForm(prefix="accept-invite")
    invitation = RoomInvitation.get(invitation_id, with_details=True)
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
    invitation.save()
    socketio.emit("room_members_updated", serialize_room_members_payload(room), to=room.socket_room)
    emit_notification_refresh_for_user(current_user.id)
    emit_notification_refresh_for_user(invitation.inviter_id)
    flash(f"You joined {room.display_name_for(current_user)}.", "success")
    return redirect(url_for("main.view_room", room_id=room.id))


@main_bp.route("/invitations/<int:invitation_id>/decline", methods=["POST"])
@login_required
def decline_room_invitation(invitation_id):
    form = ActionForm(prefix="decline-invite")
    invitation = RoomInvitation.get(invitation_id, with_details=True)
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
    invitation.save()
    emit_notification_refresh_for_user(current_user.id)
    emit_notification_refresh_for_user(invitation.inviter_id)
    flash("Invitation declined.", "success")
    return redirect(url_for("main.notifications"))


@main_bp.route("/rooms/<int:room_id>/delete", methods=["POST"])
@login_required
def delete_room(room_id):
    form = ActionForm(prefix="delete-room")
    room = Room.get(room_id)
    if room is None:
        abort(404)

    if not form.validate_on_submit():
        flash("Unable to verify the delete request.", "error")
        return redirect(url_for("main.view_room", room_id=room_id))

    if not room_can_be_deleted_by_user(room, current_user):
        flash("That room cannot be deleted.", "error")
        return redirect(url_for("main.view_room", room_id=room_id))

    room_name = room.name
    room.delete()
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
    user = User.from_row(repo.get_user_by_id(user_id))
    if user is None:
        abort(404)
    return redirect(url_for("main.admin_dashboard"))


@main_bp.route("/admin/users/<int:user_id>/logs.json")
@admin_required
def admin_user_logs_json(user_id):
    user = User.from_row(repo.get_user_by_id(user_id))
    if user is None:
        abort(404)

    logs = [
        AdminAuditLog.from_row(row)
        for row in repo.list_admin_audit_logs_for_user(user.id, limit=100)
    ]

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

        user.save()
        log_account_activity(
            action="admin_create_user",
            target_type="user",
            target_id=user.id,
            details=f"Created user '{user.username}' with role '{user.role}'.",
        )
        if not user.is_email_verified:
            dispatch_verification_email(user)
        flash("User created successfully.", "success")
        return redirect(url_for("main.admin_dashboard"))

    return render_template("admin_dashboard.html", **build_admin_context(user_form=form)), 400


@main_bp.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_user(user_id):
    user = User.from_row(repo.get_user_by_id(user_id))
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
            user.save()
            log_account_activity(
                action="admin_edit_user",
                target_type="user",
                target_id=user.id,
                details=(
                    f"Updated user '{user.username}' role '{previous_role}' -> '{user.role}', "
                    f"verified '{previous_email_verified}' -> '{user.is_email_verified}'."
                ),
            )
        else:
            user.is_email_verified = False
            user.save()
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
    user = User.from_row(repo.get_user_by_id(user_id))
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
    user.delete()
    flash("User deleted successfully.", "success")
    return redirect(url_for("main.admin_dashboard"))


@main_bp.route("/admin/rooms/<int:room_id>/delete", methods=["POST"])
@admin_required
def admin_delete_room(room_id):
    form = ActionForm(prefix="admin-room-delete")
    room = Room.get(room_id)
    if room is None:
        abort(404)

    if not form.validate_on_submit():
        flash("Unable to verify the room delete request.", "error")
        return redirect(url_for("main.admin_dashboard"))

    room_name = room.name
    log_account_activity(
        action="admin_delete_room",
        target_type="room",
        target_id=room.id,
        details=f"Deleted room '{room_name}' (private={room.is_private}).",
    )
    room.delete()
    flash(f"Room '{room_name}' deleted.", "success")
    return redirect(url_for("main.admin_dashboard"))


@main_bp.route("/users/<int:user_id>/block", methods=["POST"])
@login_required
def block_user(user_id):
    target_user = User.from_row(repo.get_user_by_id(user_id))
    if target_user is None:
        abort(404)

    if target_user.id == current_user.id:
        flash("You cannot block your own account.", "error")
        return redirect(request.referrer or url_for("main.dashboard"))

    if not repo.user_has_blocked(current_user.id, target_user.id):
        UserBlock(blocker_id=current_user.id, blocked_id=target_user.id).save()
        log_account_activity(
            action="account_block_user",
            target_type="user",
            target_id=target_user.id,
            details=f"'{current_user.username}' blocked '{target_user.username}'.",
        )

    flash(f"{target_user.username} has been blocked.", "success")
    return redirect(request.referrer or url_for("main.dashboard"))


@main_bp.route("/users/<int:user_id>/unblock", methods=["POST"])
@login_required
def unblock_user(user_id):
    target_user = User.from_row(repo.get_user_by_id(user_id))
    if target_user is None:
        abort(404)

    block_row = repo.get_user_block(current_user.id, target_user.id)
    if block_row is not None:
        UserBlock.from_row(block_row).delete()
        log_account_activity(
            action="account_unblock_user",
            target_type="user",
            target_id=target_user.id,
            details=f"'{current_user.username}' unblocked '{target_user.username}'.",
        )

    flash(f"{target_user.username} has been unblocked.", "success")
    return redirect(request.referrer or url_for("main.dashboard"))
