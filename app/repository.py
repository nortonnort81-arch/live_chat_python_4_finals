"""Raw MySQL data access. All queries use parameterized SQL."""

from datetime import datetime

from app import database as db


def _bool(value):
    return bool(value) if value is not None else False


def _normalize_user_row(row):
    if row is None:
        return None
    data = dict(row)
    data["is_email_verified"] = _bool(data.get("is_email_verified"))
    return data


def _normalize_room_row(row):
    if row is None:
        return None
    data = dict(row)
    data["is_private"] = _bool(data.get("is_private"))
    return data


# ---------------------------------------------------------------------------
# Schema helpers (used by ensure_database_schema)
# ---------------------------------------------------------------------------


def table_exists(table_name):
    row = db.fetchone(
        """
        SELECT 1 AS present
        FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = %s
        LIMIT 1
        """,
        (table_name,),
    )
    return row is not None


def list_table_columns(table_name):
    rows = db.fetchall(
        """
        SELECT column_name, column_type, character_maximum_length
        FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = %s
        """,
        (table_name,),
    )
    return {row["column_name"]: row for row in rows}


def index_exists(table_name, index_name):
    row = db.fetchone(
        """
        SELECT 1 AS present
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND index_name = %s
        LIMIT 1
        """,
        (table_name, index_name),
    )
    return row is not None


def foreign_key_exists(table_name, constraint_name):
    row = db.fetchone(
        """
        SELECT 1 AS present
        FROM information_schema.table_constraints
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND constraint_name = %s
          AND constraint_type = 'FOREIGN KEY'
        LIMIT 1
        """,
        (table_name, constraint_name),
    )
    return row is not None


def unique_constraint_exists(table_name, constraint_name):
    row = db.fetchone(
        """
        SELECT 1 AS present
        FROM information_schema.table_constraints
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND constraint_name = %s
          AND constraint_type = 'UNIQUE'
        LIMIT 1
        """,
        (table_name, constraint_name),
    )
    return row is not None


def list_unique_constraints(table_name):
    return db.fetchall(
        """
        SELECT constraint_name, column_name
        FROM information_schema.key_column_usage
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND constraint_name IN (
            SELECT constraint_name
            FROM information_schema.table_constraints
            WHERE table_schema = DATABASE()
              AND table_name = %s
              AND constraint_type = 'UNIQUE'
          )
        ORDER BY constraint_name, ordinal_position
        """,
        (table_name, table_name),
    )


def execute_ddl(sql):
    db.execute(sql)
    db.commit()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def get_user_by_id(user_id):
    row = db.fetchone("SELECT * FROM users WHERE id = %s", (user_id,))
    return _normalize_user_row(row)


def get_user_by_email(email):
    row = db.fetchone(
        "SELECT * FROM users WHERE email = %s LIMIT 1",
        ((email or "").strip().lower(),),
    )
    return _normalize_user_row(row)


def get_user_by_username(username):
    row = db.fetchone(
        "SELECT * FROM users WHERE username = %s LIMIT 1",
        ((username or "").strip(),),
    )
    return _normalize_user_row(row)


def username_exists(username, exclude_user_id=None):
    if exclude_user_id is None:
        row = db.fetchone(
            "SELECT id FROM users WHERE username = %s LIMIT 1",
            ((username or "").strip(),),
        )
    else:
        row = db.fetchone(
            "SELECT id FROM users WHERE username = %s AND id <> %s LIMIT 1",
            ((username or "").strip(), exclude_user_id),
        )
    return row is not None


def email_exists(email, exclude_user_id=None):
    normalized = (email or "").strip().lower()
    if exclude_user_id is None:
        row = db.fetchone(
            "SELECT id FROM users WHERE email = %s LIMIT 1",
            (normalized,),
        )
    else:
        row = db.fetchone(
            "SELECT id FROM users WHERE email = %s AND id <> %s LIMIT 1",
            (normalized, exclude_user_id),
        )
    return row is not None


def insert_user(
    username,
    email,
    password_hash,
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
):
    now = created_at or datetime.utcnow()
    last_seen_value = last_seen or now
    db.execute(
        """
        INSERT INTO users (
            username, email, password_hash, role, is_email_verified,
            verification_token_hash, verification_token_expires_at,
            verification_sent_at, last_seen, display_name, bio, avatar_url, created_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s,
            %s, %s, %s, %s, %s, %s
        )
        """,
        (
            username,
            email,
            password_hash,
            role,
            int(bool(is_email_verified)),
            verification_token_hash,
            verification_token_expires_at,
            verification_sent_at,
            last_seen_value,
            display_name,
            bio,
            avatar_url,
            now,
        ),
    )
    user_id = db.last_insert_id()
    db.commit()
    return user_id


def update_user(
    user_id,
    username=None,
    email=None,
    password_hash=None,
    role=None,
    is_email_verified=None,
    verification_token_hash=None,
    verification_token_expires_at=None,
    verification_sent_at=None,
    last_seen=None,
    display_name=None,
    bio=None,
    avatar_url=None,
    clear_verification=False,
):
    fields = []
    params = []

    mapping = {
        "username": username,
        "email": email,
        "password_hash": password_hash,
        "role": role,
        "display_name": display_name,
        "bio": bio,
        "avatar_url": avatar_url,
        "last_seen": last_seen,
        "verification_sent_at": verification_sent_at,
    }
    for column, value in mapping.items():
        if value is not None:
            fields.append(f"{column} = %s")
            params.append(value)

    if is_email_verified is not None:
        fields.append("is_email_verified = %s")
        params.append(int(bool(is_email_verified)))

    if verification_token_hash is not None:
        fields.append("verification_token_hash = %s")
        params.append(verification_token_hash)

    if verification_token_expires_at is not None:
        fields.append("verification_token_expires_at = %s")
        params.append(verification_token_expires_at)

    if clear_verification:
        fields.extend(
            [
                "verification_token_hash = NULL",
                "verification_token_expires_at = NULL",
                "verification_sent_at = NULL",
            ]
        )

    if not fields:
        return

    params.append(user_id)
    db.execute(
        f"UPDATE users SET {', '.join(fields)} WHERE id = %s",
        tuple(params),
    )
    db.commit()


def delete_user(user_id):
    db.execute("DELETE FROM users WHERE id = %s", (user_id,))
    db.commit()


def touch_user_last_seen(user_id, last_seen=None):
    db.execute(
        "UPDATE users SET last_seen = %s WHERE id = %s",
        (last_seen or datetime.utcnow(), user_id),
    )
    db.commit()


def list_users_excluding(user_id):
    rows = db.fetchall(
        """
        SELECT * FROM users
        WHERE id <> %s
        ORDER BY username ASC
        """,
        (user_id,),
    )
    return [_normalize_user_row(row) for row in rows]


def search_users_admin(query):
    like = f"%{(query or '').strip()}%"
    rows = db.fetchall(
        """
        SELECT * FROM users
        WHERE username LIKE %s OR email LIKE %s OR role LIKE %s
        ORDER BY created_at DESC
        """,
        (like, like, like),
    )
    return [_normalize_user_row(row) for row in rows]


def list_all_users_admin():
    rows = db.fetchall("SELECT * FROM users ORDER BY created_at DESC")
    return [_normalize_user_row(row) for row in rows]


def count_all_users():
    row = db.fetchone("SELECT COUNT(*) AS total FROM users")
    return int(row["total"]) if row else 0


def count_verified_users():
    row = db.fetchone(
        "SELECT COUNT(*) AS total FROM users WHERE is_email_verified = 1"
    )
    return int(row["total"]) if row else 0


def count_users_with_role(role):
    row = db.fetchone(
        "SELECT COUNT(*) AS total FROM users WHERE role = %s",
        (role,),
    )
    return int(row["total"]) if row else 0


# ---------------------------------------------------------------------------
# Rooms
# ---------------------------------------------------------------------------


def get_room_by_id(room_id):
    row = db.fetchone("SELECT * FROM rooms WHERE id = %s", (room_id,))
    return _normalize_room_row(row)


def get_room_by_name(name):
    row = db.fetchone("SELECT * FROM rooms WHERE name = %s LIMIT 1", (name,))
    return _normalize_room_row(row)


def insert_room(name, is_private=False, owner_id=None, created_at=None):
    now = created_at or datetime.utcnow()
    db.execute(
        """
        INSERT INTO rooms (name, is_private, owner_id, created_at)
        VALUES (%s, %s, %s, %s)
        """,
        (name, int(bool(is_private)), owner_id, now),
    )
    room_id = db.last_insert_id()
    db.commit()
    return room_id


def update_room(room_id, name=None, is_private=None, owner_id=None):
    fields = []
    params = []
    if name is not None:
        fields.append("name = %s")
        params.append(name)
    if is_private is not None:
        fields.append("is_private = %s")
        params.append(int(bool(is_private)))
    if owner_id is not None:
        fields.append("owner_id = %s")
        params.append(owner_id)
    if not fields:
        return
    params.append(room_id)
    db.execute(
        f"UPDATE rooms SET {', '.join(fields)} WHERE id = %s",
        tuple(params),
    )
    db.commit()


def delete_room(room_id):
    db.execute("DELETE FROM rooms WHERE id = %s", (room_id,))
    db.commit()


def list_public_rooms():
    rows = db.fetchall(
        """
        SELECT * FROM rooms
        WHERE is_private = 0
        ORDER BY name ASC
        """
    )
    return [_normalize_room_row(row) for row in rows]


def list_private_rooms_for_user(user_id):
    rows = db.fetchall(
        """
        SELECT r.*
        FROM rooms r
        INNER JOIN room_members rm ON rm.room_id = r.id
        WHERE r.is_private = 1 AND rm.user_id = %s
        ORDER BY r.created_at DESC
        """,
        (user_id,),
    )
    return [_normalize_room_row(row) for row in rows]


def search_rooms_admin(query):
    like = f"%{(query or '').strip()}%"
    rows = db.fetchall(
        """
        SELECT * FROM rooms
        WHERE name LIKE %s
        ORDER BY created_at DESC
        """,
        (like,),
    )
    return [_normalize_room_row(row) for row in rows]


def list_all_rooms_admin():
    rows = db.fetchall("SELECT * FROM rooms ORDER BY created_at DESC")
    return [_normalize_room_row(row) for row in rows]


def count_all_rooms():
    row = db.fetchone("SELECT COUNT(*) AS total FROM rooms")
    return int(row["total"]) if row else 0


# ---------------------------------------------------------------------------
# Room members
# ---------------------------------------------------------------------------


def is_room_member(room_id, user_id):
    row = db.fetchone(
        """
        SELECT id FROM room_members
        WHERE room_id = %s AND user_id = %s
        LIMIT 1
        """,
        (room_id, user_id),
    )
    return row is not None


def add_room_member(room_id, user_id, joined_at=None):
    if is_room_member(room_id, user_id):
        return
    db.execute(
        """
        INSERT INTO room_members (user_id, room_id, joined_at)
        VALUES (%s, %s, %s)
        """,
        (user_id, room_id, joined_at or datetime.utcnow()),
    )
    db.commit()


def remove_room_member(room_id, user_id):
    db.execute(
        "DELETE FROM room_members WHERE room_id = %s AND user_id = %s",
        (room_id, user_id),
    )
    db.commit()


def list_room_members(room_id):
    rows = db.fetchall(
        """
        SELECT rm.id, rm.user_id, rm.room_id, rm.joined_at,
               u.id AS user_id, u.username, u.email, u.role,
               u.is_email_verified, u.last_seen, u.display_name, u.bio, u.avatar_url,
               u.created_at AS user_created_at
        FROM room_members rm
        INNER JOIN users u ON u.id = rm.user_id
        WHERE rm.room_id = %s
        ORDER BY rm.joined_at ASC
        """,
        (room_id,),
    )
    members = []
    for row in rows:
        members.append(
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "room_id": row["room_id"],
                "joined_at": row["joined_at"],
                "user": _normalize_user_row(
                    {
                        "id": row["user_id"],
                        "username": row["username"],
                        "email": row["email"],
                        "role": row["role"],
                        "is_email_verified": row["is_email_verified"],
                        "last_seen": row["last_seen"],
                        "display_name": row["display_name"],
                        "bio": row["bio"],
                        "avatar_url": row["avatar_url"],
                        "created_at": row["user_created_at"],
                    }
                ),
            }
        )
    return members


def list_room_member_user_ids(room_id):
    rows = db.fetchall(
        "SELECT user_id FROM room_members WHERE room_id = %s",
        (room_id,),
    )
    return [row["user_id"] for row in rows]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def get_message_by_id(message_id):
    row = db.fetchone("SELECT * FROM messages WHERE id = %s", (message_id,))
    return dict(row) if row else None


def insert_message(user_id, room_id, content, message_type="text", created_at=None):
    now = created_at or datetime.utcnow()
    db.execute(
        """
        INSERT INTO messages (user_id, room_id, content, message_type, created_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (user_id, room_id, content, message_type, now),
    )
    message_id = db.last_insert_id()
    db.commit()
    return message_id


def list_room_messages_ordered(room_id):
    rows = db.fetchall(
        """
        SELECT m.*, u.id AS author_id, u.username AS author_username
        FROM messages m
        INNER JOIN users u ON u.id = m.user_id
        WHERE m.room_id = %s
        ORDER BY m.created_at ASC
        """,
        (room_id,),
    )
    return [dict(row) for row in rows]


def count_all_messages():
    row = db.fetchone("SELECT COUNT(*) AS total FROM messages")
    return int(row["total"]) if row else 0


def list_unread_messages_for_room(room_id, user_id):
    rows = db.fetchall(
        """
        SELECT m.*
        FROM messages m
        WHERE m.room_id = %s
          AND m.user_id <> %s
          AND NOT EXISTS (
            SELECT 1 FROM message_reads mr
            WHERE mr.message_id = m.id AND mr.user_id = %s
          )
        ORDER BY m.created_at ASC
        """,
        (room_id, user_id, user_id),
    )
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Message reads
# ---------------------------------------------------------------------------


def is_message_read_by_user(message_id, user_id):
    row = db.fetchone(
        """
        SELECT id FROM message_reads
        WHERE message_id = %s AND user_id = %s
        LIMIT 1
        """,
        (message_id, user_id),
    )
    return row is not None


def insert_message_read(message_id, user_id, read_at=None):
    if is_message_read_by_user(message_id, user_id):
        return
    db.execute(
        """
        INSERT INTO message_reads (message_id, user_id, read_at)
        VALUES (%s, %s, %s)
        """,
        (message_id, user_id, read_at or datetime.utcnow()),
    )
    db.commit()


def list_message_reads_with_users(message_id):
    rows = db.fetchall(
        """
        SELECT mr.*, u.username
        FROM message_reads mr
        INNER JOIN users u ON u.id = mr.user_id
        WHERE mr.message_id = %s
        ORDER BY mr.read_at ASC
        """,
        (message_id,),
    )
    return [dict(row) for row in rows]


def list_message_reads_for_messages(message_ids):
    if not message_ids:
        return []
    placeholders = ", ".join(["%s"] * len(message_ids))
    rows = db.fetchall(
        f"""
        SELECT mr.*, u.username
        FROM message_reads mr
        INNER JOIN users u ON u.id = mr.user_id
        WHERE mr.message_id IN ({placeholders})
        ORDER BY mr.read_at ASC
        """,
        tuple(message_ids),
    )
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Message mentions
# ---------------------------------------------------------------------------


def insert_message_mention(message_id, user_id, created_at=None):
    db.execute(
        """
        INSERT IGNORE INTO message_mentions (message_id, user_id, created_at)
        VALUES (%s, %s, %s)
        """,
        (message_id, user_id, created_at or datetime.utcnow()),
    )
    db.commit()


def list_mentions_for_message(message_id):
    rows = db.fetchall(
        "SELECT * FROM message_mentions WHERE message_id = %s",
        (message_id,),
    )
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# User blocks
# ---------------------------------------------------------------------------


def users_are_blocked(first_user_id, second_user_id):
    row = db.fetchone(
        """
        SELECT id FROM user_blocks
        WHERE (blocker_id = %s AND blocked_id = %s)
           OR (blocker_id = %s AND blocked_id = %s)
        LIMIT 1
        """,
        (first_user_id, second_user_id, second_user_id, first_user_id),
    )
    return row is not None


def user_has_blocked(blocker_id, blocked_id):
    row = db.fetchone(
        """
        SELECT id FROM user_blocks
        WHERE blocker_id = %s AND blocked_id = %s
        LIMIT 1
        """,
        (blocker_id, blocked_id),
    )
    return row is not None


def list_blocks_initiated_by(user_id):
    rows = db.fetchall(
        "SELECT * FROM user_blocks WHERE blocker_id = %s",
        (user_id,),
    )
    return [dict(row) for row in rows]


def list_blocks_received_by(user_id):
    rows = db.fetchall(
        "SELECT * FROM user_blocks WHERE blocked_id = %s",
        (user_id,),
    )
    return [dict(row) for row in rows]


def get_user_block(blocker_id, blocked_id):
    row = db.fetchone(
        """
        SELECT * FROM user_blocks
        WHERE blocker_id = %s AND blocked_id = %s
        LIMIT 1
        """,
        (blocker_id, blocked_id),
    )
    return dict(row) if row else None


def insert_user_block(blocker_id, blocked_id, created_at=None):
    db.execute(
        """
        INSERT IGNORE INTO user_blocks (blocker_id, blocked_id, created_at)
        VALUES (%s, %s, %s)
        """,
        (blocker_id, blocked_id, created_at or datetime.utcnow()),
    )
    db.commit()


def delete_user_block(blocker_id, blocked_id):
    db.execute(
        "DELETE FROM user_blocks WHERE blocker_id = %s AND blocked_id = %s",
        (blocker_id, blocked_id),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Room invitations
# ---------------------------------------------------------------------------


def get_room_invitation_by_id(invitation_id):
    row = db.fetchone(
        "SELECT * FROM room_invitations WHERE id = %s",
        (invitation_id,),
    )
    return dict(row) if row else None


def get_room_invitation_by_room_and_invitee(room_id, invitee_id):
    row = db.fetchone(
        """
        SELECT * FROM room_invitations
        WHERE room_id = %s AND invitee_id = %s
        LIMIT 1
        """,
        (room_id, invitee_id),
    )
    return dict(row) if row else None


def insert_room_invitation(room_id, inviter_id, invitee_id, status="pending", created_at=None):
    now = created_at or datetime.utcnow()
    db.execute(
        """
        INSERT INTO room_invitations (
            room_id, inviter_id, invitee_id, status, created_at, responded_at
        ) VALUES (%s, %s, %s, %s, %s, NULL)
        """,
        (room_id, inviter_id, invitee_id, status, now),
    )
    invitation_id = db.last_insert_id()
    db.commit()
    return invitation_id


def update_room_invitation(
    invitation_id,
    inviter_id=None,
    status=None,
    responded_at=None,
    created_at=None,
):
    fields = []
    params = []
    if inviter_id is not None:
        fields.append("inviter_id = %s")
        params.append(inviter_id)
    if status is not None:
        fields.append("status = %s")
        params.append(status)
    if responded_at is not None:
        fields.append("responded_at = %s")
        params.append(responded_at)
    if created_at is not None:
        fields.append("created_at = %s")
        params.append(created_at)
    if not fields:
        return
    params.append(invitation_id)
    db.execute(
        f"UPDATE room_invitations SET {', '.join(fields)} WHERE id = %s",
        tuple(params),
    )
    db.commit()


def delete_room_invitation(invitation_id):
    db.execute("DELETE FROM room_invitations WHERE id = %s", (invitation_id,))
    db.commit()


def delete_room_invitations_for_room(room_id):
    db.execute("DELETE FROM room_invitations WHERE room_id = %s", (room_id,))
    db.commit()


def list_room_invitations_for_room(room_id):
    rows = db.fetchall(
        "SELECT * FROM room_invitations WHERE room_id = %s",
        (room_id,),
    )
    return [dict(row) for row in rows]


def find_duplicate_invitation_pairs():
    rows = db.fetchall(
        """
        SELECT room_id, invitee_id
        FROM room_invitations
        GROUP BY room_id, invitee_id
        HAVING COUNT(*) > 1
        """
    )
    return [(row["room_id"], row["invitee_id"]) for row in rows]


def list_invitations_for_room_invitee(room_id, invitee_id):
    rows = db.fetchall(
        """
        SELECT * FROM room_invitations
        WHERE room_id = %s AND invitee_id = %s
        ORDER BY id ASC
        """,
        (room_id, invitee_id),
    )
    return [dict(row) for row in rows]


def delete_room_invitations_except(room_id, invitee_id, keep_id):
    db.execute(
        """
        DELETE FROM room_invitations
        WHERE room_id = %s AND invitee_id = %s AND id <> %s
        """,
        (room_id, invitee_id, keep_id),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Admin audit logs
# ---------------------------------------------------------------------------


def insert_admin_audit_log(
    actor_id,
    actor_role,
    action,
    target_type=None,
    target_id=None,
    details=None,
    created_at=None,
):
    db.execute(
        """
        INSERT INTO admin_audit_logs (
            actor_id, actor_role, action, target_type, target_id, details, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            actor_id,
            actor_role,
            action,
            target_type,
            target_id,
            details,
            created_at or datetime.utcnow(),
        ),
    )
    db.commit()


def list_admin_audit_logs(query=None, limit=100):
    if query:
        like = f"%{query.strip()}%"
        rows = db.fetchall(
            """
            SELECT al.*, u.username AS actor_username
            FROM admin_audit_logs al
            LEFT JOIN users u ON u.id = al.actor_id
            WHERE al.action LIKE %s
               OR al.actor_role LIKE %s
               OR al.target_type LIKE %s
               OR al.details LIKE %s
            ORDER BY al.created_at DESC
            LIMIT %s
            """,
            (like, like, like, like, limit),
        )
    else:
        rows = db.fetchall(
            """
            SELECT al.*, u.username AS actor_username
            FROM admin_audit_logs al
            LEFT JOIN users u ON u.id = al.actor_id
            ORDER BY al.created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
    return [dict(row) for row in rows]


def list_admin_audit_logs_for_user(user_id, limit=100):
    rows = db.fetchall(
        """
        SELECT al.*, u.username AS actor_username
        FROM admin_audit_logs al
        LEFT JOIN users u ON u.id = al.actor_id
        WHERE al.actor_id = %s
           OR (al.target_type = 'user' AND al.target_id = %s)
        ORDER BY al.created_at DESC
        LIMIT %s
        """,
        (user_id, user_id, limit),
    )
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Notification queries
# ---------------------------------------------------------------------------


def fetch_unread_message_notifications(user_id, limit=None):
    sql = """
        SELECT m.id, m.room_id, m.content, m.created_at, m.message_type,
               r.name AS room_name, r.is_private,
               u.username AS sender_username
        FROM messages m
        INNER JOIN room_members rm ON rm.room_id = m.room_id AND rm.user_id = %s
        INNER JOIN rooms r ON r.id = m.room_id
        INNER JOIN users u ON u.id = m.user_id
        WHERE m.user_id <> %s
          AND NOT EXISTS (
            SELECT 1 FROM message_reads mr
            WHERE mr.message_id = m.id AND mr.user_id = %s
          )
          AND NOT EXISTS (
            SELECT 1 FROM message_mentions mm
            WHERE mm.message_id = m.id AND mm.user_id = %s
          )
        ORDER BY m.created_at DESC
    """
    params = [user_id, user_id, user_id, user_id]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    rows = db.fetchall(sql, tuple(params))
    results = []
    for row in rows:
        data = dict(row)
        data["is_private"] = _bool(data.get("is_private"))
        results.append(data)
    return results


def fetch_unread_mention_notifications(user_id, limit=None):
    sql = """
        SELECT mm.id AS mention_id, mm.created_at AS mention_created_at,
               m.id AS message_id, m.room_id, m.content, m.created_at AS message_created_at,
               r.name AS room_name, r.is_private,
               u.username AS sender_username
        FROM message_mentions mm
        INNER JOIN messages m ON m.id = mm.message_id
        INNER JOIN rooms r ON r.id = m.room_id
        INNER JOIN users u ON u.id = m.user_id
        WHERE mm.user_id = %s
          AND NOT EXISTS (
            SELECT 1 FROM message_reads mr
            WHERE mr.message_id = mm.message_id AND mr.user_id = %s
          )
        ORDER BY mm.created_at DESC
    """
    params = [user_id, user_id]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    rows = db.fetchall(sql, tuple(params))
    results = []
    for row in rows:
        data = dict(row)
        data["is_private"] = _bool(data.get("is_private"))
        results.append(data)
    return results


def fetch_pending_invitation_notifications(user_id, limit=None):
    sql = """
        SELECT ri.*,
               r.name AS room_name, r.is_private,
               inviter.username AS inviter_username
        FROM room_invitations ri
        INNER JOIN rooms r ON r.id = ri.room_id
        INNER JOIN users inviter ON inviter.id = ri.inviter_id
        WHERE ri.invitee_id = %s AND ri.status = 'pending'
        ORDER BY ri.created_at DESC
    """
    params = [user_id]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    rows = db.fetchall(sql, tuple(params))
    results = []
    for row in rows:
        data = dict(row)
        data["is_private"] = _bool(data.get("is_private"))
        results.append(data)
    return results


# ---------------------------------------------------------------------------
# Admin stats
# ---------------------------------------------------------------------------


def get_admin_dashboard_stats():
    return {
        "total_users": count_all_users(),
        "verified_users": count_verified_users(),
        "admin_users": count_users_with_role("admin"),
        "super_admin_users": count_users_with_role("super_admin"),
        "total_messages": count_all_messages(),
        "total_rooms": count_all_rooms(),
    }


# ---------------------------------------------------------------------------
# Composite loaders
# ---------------------------------------------------------------------------


def fetch_message_with_details(message_id):
    message_row = get_message_by_id(message_id)
    if message_row is None:
        return None

    user_row = get_user_by_id(message_row["user_id"])
    room_row = get_room_by_id(message_row["room_id"])
    reads = list_message_reads_with_users(message_id)
    members = list_room_members(message_row["room_id"]) if room_row else []

    return {
        "message": message_row,
        "user": user_row,
        "room": room_row,
        "reads": reads,
        "members": members,
    }


def fetch_room_with_members_and_messages(room_id):
    room_row = get_room_by_id(room_id)
    if room_row is None:
        return None

    members = list_room_members(room_id)
    message_rows = list_room_messages_ordered(room_id)
    message_ids = [row["id"] for row in message_rows]
    read_rows = list_message_reads_for_messages(message_ids)

    reads_by_message = {}
    for read_row in read_rows:
        reads_by_message.setdefault(read_row["message_id"], []).append(read_row)

    messages = []
    for message_row in message_rows:
        messages.append(
            {
                "message": message_row,
                "user": _normalize_user_row(
                    {
                        "id": message_row["author_id"],
                        "username": message_row["author_username"],
                    }
                ),
                "reads": reads_by_message.get(message_row["id"], []),
            }
        )

    return {
        "room": room_row,
        "members": members,
        "messages": messages,
        "owner": get_user_by_id(room_row["owner_id"]) if room_row.get("owner_id") else None,
    }


def fetch_room_invitation_with_details(invitation_id):
    row = get_room_invitation_by_id(invitation_id)
    if row is None:
        return None
    return {
        "invitation": row,
        "room": get_room_by_id(row["room_id"]),
        "inviter": get_user_by_id(row["inviter_id"]),
        "invitee": get_user_by_id(row["invitee_id"]),
        "members": list_room_members(row["room_id"]),
    }


def create_private_dm_room(name, user_ids):
    room_id = insert_room(name, is_private=True)
    for user_id in user_ids:
        add_room_member(room_id, user_id)
    return room_id
