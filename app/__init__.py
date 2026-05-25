import os

from flask import Flask
from flask_login import LoginManager
from flask_socketio import SocketIO
from dotenv import load_dotenv


load_dotenv(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
    override=True,
)

from config import Config


login_manager = LoginManager()
socketio = SocketIO()


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INT UNSIGNED NOT NULL AUTO_INCREMENT,
        username VARCHAR(80) NOT NULL,
        email VARCHAR(255) NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        role VARCHAR(20) NOT NULL DEFAULT 'user',
        is_email_verified TINYINT(1) NOT NULL DEFAULT 0,
        verification_token_hash VARCHAR(128) DEFAULT NULL,
        verification_token_expires_at DATETIME DEFAULT NULL,
        verification_sent_at DATETIME DEFAULT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        display_name VARCHAR(120) DEFAULT NULL,
        bio TEXT DEFAULT NULL,
        avatar_url VARCHAR(255) DEFAULT NULL,
        PRIMARY KEY (id),
        UNIQUE KEY uq_users_username (username),
        UNIQUE KEY uq_users_email (email),
        KEY ix_users_username (username),
        KEY ix_users_email (email),
        KEY ix_users_role (role)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS rooms (
        id INT UNSIGNED NOT NULL AUTO_INCREMENT,
        name VARCHAR(120) NOT NULL,
        is_private TINYINT(1) NOT NULL DEFAULT 0,
        owner_id INT UNSIGNED DEFAULT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uq_rooms_name (name),
        KEY ix_rooms_owner_id (owner_id),
        CONSTRAINT fk_rooms_owner
            FOREIGN KEY (owner_id) REFERENCES users (id)
            ON DELETE SET NULL ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS room_members (
        id INT UNSIGNED NOT NULL AUTO_INCREMENT,
        user_id INT UNSIGNED NOT NULL,
        room_id INT UNSIGNED NOT NULL,
        joined_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uq_room_member_user_room (user_id, room_id),
        KEY ix_room_members_room_id (room_id),
        CONSTRAINT fk_room_members_user
            FOREIGN KEY (user_id) REFERENCES users (id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        CONSTRAINT fk_room_members_room
            FOREIGN KEY (room_id) REFERENCES rooms (id)
            ON DELETE CASCADE ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id INT UNSIGNED NOT NULL AUTO_INCREMENT,
        user_id INT UNSIGNED NOT NULL,
        room_id INT UNSIGNED NOT NULL,
        content TEXT NOT NULL,
        message_type VARCHAR(50) NOT NULL DEFAULT 'text',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY ix_messages_room_id_created_at (room_id, created_at),
        KEY ix_messages_user_id (user_id),
        CONSTRAINT fk_messages_user
            FOREIGN KEY (user_id) REFERENCES users (id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        CONSTRAINT fk_messages_room
            FOREIGN KEY (room_id) REFERENCES rooms (id)
            ON DELETE CASCADE ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS message_reads (
        id INT UNSIGNED NOT NULL AUTO_INCREMENT,
        message_id INT UNSIGNED NOT NULL,
        user_id INT UNSIGNED NOT NULL,
        read_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uq_message_read_user (message_id, user_id),
        KEY ix_message_reads_user_id (user_id),
        CONSTRAINT fk_message_reads_message
            FOREIGN KEY (message_id) REFERENCES messages (id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        CONSTRAINT fk_message_reads_user
            FOREIGN KEY (user_id) REFERENCES users (id)
            ON DELETE CASCADE ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS message_mentions (
        id INT UNSIGNED NOT NULL AUTO_INCREMENT,
        message_id INT UNSIGNED NOT NULL,
        user_id INT UNSIGNED NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uq_message_mention_user (message_id, user_id),
        KEY ix_message_mentions_message_id (message_id),
        KEY ix_message_mentions_user_id (user_id),
        CONSTRAINT fk_message_mentions_message
            FOREIGN KEY (message_id) REFERENCES messages (id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        CONSTRAINT fk_message_mentions_user
            FOREIGN KEY (user_id) REFERENCES users (id)
            ON DELETE CASCADE ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS user_blocks (
        id INT UNSIGNED NOT NULL AUTO_INCREMENT,
        blocker_id INT UNSIGNED NOT NULL,
        blocked_id INT UNSIGNED NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uq_user_block_pair (blocker_id, blocked_id),
        KEY ix_user_blocks_blocker_id (blocker_id),
        KEY ix_user_blocks_blocked_id (blocked_id),
        CONSTRAINT fk_user_blocks_blocker
            FOREIGN KEY (blocker_id) REFERENCES users (id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        CONSTRAINT fk_user_blocks_blocked
            FOREIGN KEY (blocked_id) REFERENCES users (id)
            ON DELETE CASCADE ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS room_invitations (
        id INT UNSIGNED NOT NULL AUTO_INCREMENT,
        room_id INT UNSIGNED NOT NULL,
        inviter_id INT UNSIGNED NOT NULL,
        invitee_id INT UNSIGNED NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        responded_at DATETIME DEFAULT NULL,
        PRIMARY KEY (id),
        UNIQUE KEY uq_room_invitation_room_invitee (room_id, invitee_id),
        KEY ix_room_invitations_room_id (room_id),
        KEY ix_room_invitations_inviter_id (inviter_id),
        KEY ix_room_invitations_invitee_id (invitee_id),
        KEY ix_room_invitations_status (status),
        CONSTRAINT fk_room_invitations_room
            FOREIGN KEY (room_id) REFERENCES rooms (id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        CONSTRAINT fk_room_invitations_inviter
            FOREIGN KEY (inviter_id) REFERENCES users (id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        CONSTRAINT fk_room_invitations_invitee
            FOREIGN KEY (invitee_id) REFERENCES users (id)
            ON DELETE CASCADE ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS admin_audit_logs (
        id INT UNSIGNED NOT NULL AUTO_INCREMENT,
        actor_id INT UNSIGNED DEFAULT NULL,
        actor_role VARCHAR(20) NOT NULL,
        action VARCHAR(120) NOT NULL,
        target_type VARCHAR(60) DEFAULT NULL,
        target_id INT UNSIGNED DEFAULT NULL,
        details TEXT DEFAULT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY ix_admin_audit_logs_actor_id (actor_id),
        KEY ix_admin_audit_logs_actor_role (actor_role),
        KEY ix_admin_audit_logs_action (action),
        KEY ix_admin_audit_logs_target_type (target_type),
        KEY ix_admin_audit_logs_target_id (target_id),
        KEY ix_admin_audit_logs_created_at (created_at),
        CONSTRAINT fk_admin_audit_logs_actor
            FOREIGN KEY (actor_id) REFERENCES users (id)
            ON DELETE SET NULL ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


def ensure_database_schema():
    from app import database as db
    from app import repository as repo

    for statement in SCHEMA_STATEMENTS:
        db.execute(statement)
    db.commit()

    if not repo.table_exists("users"):
        return

    user_columns = repo.list_table_columns("users")
    user_column_names = set(user_columns.keys())
    statements = []

    if "role" not in user_column_names:
        statements.append(
            "ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'"
        )
    if "is_email_verified" not in user_column_names:
        statements.append(
            "ALTER TABLE users ADD COLUMN is_email_verified TINYINT(1) NOT NULL DEFAULT 0"
        )
    if "verification_token_hash" not in user_column_names:
        statements.append(
            "ALTER TABLE users ADD COLUMN verification_token_hash VARCHAR(128) NULL"
        )
    if "verification_token_expires_at" not in user_column_names:
        statements.append(
            "ALTER TABLE users ADD COLUMN verification_token_expires_at DATETIME NULL"
        )
    if "verification_sent_at" not in user_column_names:
        statements.append(
            "ALTER TABLE users ADD COLUMN verification_sent_at DATETIME NULL"
        )
    if "last_seen" not in user_column_names:
        statements.append(
            "ALTER TABLE users ADD COLUMN last_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        )
    if "created_at" not in user_column_names:
        statements.append(
            "ALTER TABLE users ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        )
    if "display_name" not in user_column_names:
        statements.append(
            "ALTER TABLE users ADD COLUMN display_name VARCHAR(120) NULL"
        )
    if "bio" not in user_column_names:
        statements.append("ALTER TABLE users ADD COLUMN bio TEXT NULL")
    if "avatar_url" not in user_column_names:
        statements.append(
            "ALTER TABLE users ADD COLUMN avatar_url VARCHAR(255) NULL"
        )

    for statement in statements:
        repo.execute_ddl(statement)

    verification_column = user_columns.get("verification_token_hash")
    verification_column_length = verification_column.get("character_maximum_length") if verification_column else None
    if verification_column_length is not None and verification_column_length < 128:
        repo.execute_ddl(
            "ALTER TABLE users MODIFY COLUMN verification_token_hash VARCHAR(128) NULL"
        )

    if not repo.index_exists("users", "ix_users_role"):
        repo.execute_ddl("CREATE INDEX ix_users_role ON users (role)")

    if repo.table_exists("rooms"):
        room_columns = repo.list_table_columns("rooms")
        if "owner_id" not in room_columns:
            repo.execute_ddl(
                "ALTER TABLE rooms ADD COLUMN owner_id INT UNSIGNED NULL"
            )
        if not repo.foreign_key_exists("rooms", "fk_rooms_owner"):
            repo.execute_ddl(
                """
                ALTER TABLE rooms
                ADD CONSTRAINT fk_rooms_owner
                FOREIGN KEY (owner_id) REFERENCES users (id)
                ON DELETE SET NULL ON UPDATE CASCADE
                """
            )
        if not repo.index_exists("rooms", "ix_rooms_owner_id"):
            repo.execute_ddl("CREATE INDEX ix_rooms_owner_id ON rooms (owner_id)")

    if repo.table_exists("room_invitations"):
        pending_first = {"pending": 0, "accepted": 1, "declined": 2}
        for room_id, invitee_id in repo.find_duplicate_invitation_pairs():
            rows = repo.list_invitations_for_room_invitee(room_id, invitee_id)
            rows.sort(key=lambda row: (pending_first.get(row["status"], 9), -row["id"]))
            keep_id = rows[0]["id"]
            repo.delete_room_invitations_except(room_id, invitee_id, keep_id)

        unique_rows = repo.list_unique_constraints("room_invitations")
        constraints = {}
        for row in unique_rows:
            constraints.setdefault(row["constraint_name"], []).append(row["column_name"])

        triple_names = [
            name
            for name, columns in constraints.items()
            if set(columns) == {"room_id", "invitee_id", "status"}
        ]
        has_pair_uc = any(
            set(columns) == {"room_id", "invitee_id"} for columns in constraints.values()
        )

        for constraint_name in triple_names:
            repo.execute_ddl(
                f"ALTER TABLE room_invitations DROP INDEX `{constraint_name}`"
            )
            has_pair_uc = repo.unique_constraint_exists(
                "room_invitations", "uq_room_invitation_room_invitee"
            )

        if not has_pair_uc and not repo.unique_constraint_exists(
            "room_invitations", "uq_room_invitation_room_invitee"
        ):
            repo.execute_ddl(
                """
                ALTER TABLE room_invitations
                ADD CONSTRAINT uq_room_invitation_room_invitee
                UNIQUE (room_id, invitee_id)
                """
            )


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    from app import database as db

    app.teardown_appcontext(db.close_db)

    login_manager.init_app(app)
    socketio.init_app(
        app,
        async_mode=app.config["SOCKETIO_ASYNC_MODE"],
        manage_session=False,
    )

    login_manager.login_view = "main.login"

    from app.routes import main_bp
    from app.sockets import register_socket_events

    app.register_blueprint(main_bp)
    register_socket_events(socketio)

    with app.app_context():
        ensure_database_schema()

    return app
