import os

from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from sqlalchemy import func, inspect, text


load_dotenv(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
    override=True,
)

from config import Config


db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
socketio = SocketIO()


def ensure_database_schema():
    inspector = inspect(db.engine)
    db.create_all()

    if "users" not in inspector.get_table_names():
        return

    user_column_map = {column["name"]: column for column in inspector.get_columns("users")}
    user_columns = set(user_column_map.keys())
    statements = []

    if "role" not in user_columns:
        statements.append(
            "ALTER TABLE users "
            "ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'"
        )
    if "is_email_verified" not in user_columns:
        statements.append(
            "ALTER TABLE users "
            "ADD COLUMN is_email_verified TINYINT(1) NOT NULL DEFAULT 0"
        )
    if "verification_token_hash" not in user_columns:
        statements.append(
            "ALTER TABLE users "
            "ADD COLUMN verification_token_hash VARCHAR(128) NULL"
        )
    if "verification_token_expires_at" not in user_columns:
        statements.append(
            "ALTER TABLE users "
            "ADD COLUMN verification_token_expires_at DATETIME NULL"
        )
    if "verification_sent_at" not in user_columns:
        statements.append(
            "ALTER TABLE users "
            "ADD COLUMN verification_sent_at DATETIME NULL"
        )
    if "last_seen" not in user_columns:
        statements.append(
            "ALTER TABLE users "
            "ADD COLUMN last_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        )
    if "created_at" not in user_columns:
        statements.append(
            "ALTER TABLE users "
            "ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        )
    if "display_name" not in user_columns:
        statements.append(
            "ALTER TABLE users "
            "ADD COLUMN display_name VARCHAR(120) NULL"
        )
    if "bio" not in user_columns:
        statements.append(
            "ALTER TABLE users "
            "ADD COLUMN bio TEXT NULL"
        )
    if "avatar_url" not in user_columns:
        statements.append(
            "ALTER TABLE users "
            "ADD COLUMN avatar_url VARCHAR(255) NULL"
        )

    for statement in statements:
        db.session.execute(text(statement))

    if statements:
        db.session.commit()

    verification_column = user_column_map.get("verification_token_hash")
    verification_column_length = (
        getattr(verification_column.get("type"), "length", None)
        if verification_column is not None
        else None
    )
    if verification_column_length is not None and verification_column_length < 128:
        db.session.execute(
            text(
                "ALTER TABLE users "
                "MODIFY COLUMN verification_token_hash VARCHAR(128) NULL"
            )
        )
        db.session.commit()

    inspector = inspect(db.engine)
    user_indexes = {index["name"] for index in inspector.get_indexes("users")}
    if "ix_users_role" not in user_indexes:
        db.session.execute(text("CREATE INDEX ix_users_role ON users (role)"))
        db.session.commit()

    inspector = inspect(db.engine)
    if "rooms" in inspector.get_table_names():
        room_columns = {column["name"] for column in inspector.get_columns("rooms")}
        if "owner_id" not in room_columns:
            db.session.execute(
                text(
                    "ALTER TABLE rooms "
                    "ADD COLUMN owner_id INT UNSIGNED NULL"
                )
            )
            db.session.commit()
        inspector = inspect(db.engine)
        room_foreign_keys = {fk["name"] for fk in inspector.get_foreign_keys("rooms")}
        if "fk_rooms_owner" not in room_foreign_keys:
            db.session.execute(
                text(
                    "ALTER TABLE rooms "
                    "ADD CONSTRAINT fk_rooms_owner "
                    "FOREIGN KEY (owner_id) REFERENCES users (id) "
                    "ON DELETE SET NULL ON UPDATE CASCADE"
                )
            )
            db.session.commit()

        inspector = inspect(db.engine)
        room_indexes = {index["name"] for index in inspector.get_indexes("rooms")}
        if "ix_rooms_owner_id" not in room_indexes:
            db.session.execute(text("CREATE INDEX ix_rooms_owner_id ON rooms (owner_id)"))
            db.session.commit()

    inspector = inspect(db.engine)
    if "room_invitations" in inspector.get_table_names():
        from sqlalchemy import select

        from app.models import RoomInvitation

        duplicate_pairs = db.session.execute(
            select(RoomInvitation.room_id, RoomInvitation.invitee_id)
            .group_by(RoomInvitation.room_id, RoomInvitation.invitee_id)
            .having(func.count() > 1)
        ).all()

        if duplicate_pairs:
            pending_first = {"pending": 0, "accepted": 1, "declined": 2}
            for room_id, invitee_id in duplicate_pairs:
                rows = db.session.scalars(
                    select(RoomInvitation).where(
                        RoomInvitation.room_id == room_id,
                        RoomInvitation.invitee_id == invitee_id,
                    )
                ).all()
                rows.sort(key=lambda row: (pending_first.get(row.status, 9), -row.id))
                keep_id = rows[0].id
                for row in rows:
                    if row.id != keep_id:
                        db.session.delete(row)
            db.session.commit()

        def _unique_invitation_uc_columns(uc):
            return frozenset(uc["column_names"])

        inspector = inspect(db.engine)
        invites_ucs = inspector.get_unique_constraints("room_invitations")
        triple_uc = next(
            (
                uc
                for uc in invites_ucs
                if _unique_invitation_uc_columns(uc) == {"room_id", "invitee_id", "status"}
            ),
            None,
        )
        has_pair_uc = any(
            _unique_invitation_uc_columns(uc) == {"room_id", "invitee_id"} for uc in invites_ucs
        )

        if triple_uc is not None:
            db.session.execute(
                text(f'ALTER TABLE room_invitations DROP INDEX `{triple_uc["name"]}`')
            )
            db.session.commit()
            inspector = inspect(db.engine)
            invites_ucs = inspector.get_unique_constraints("room_invitations")
            has_pair_uc = any(
                _unique_invitation_uc_columns(uc) == {"room_id", "invitee_id"}
                for uc in invites_ucs
            )

        if not has_pair_uc:
            db.session.execute(
                text(
                    "ALTER TABLE room_invitations "
                    "ADD CONSTRAINT uq_room_invitation_room_invitee "
                    "UNIQUE (room_id, invitee_id)"
                )
            )
            db.session.commit()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    from app import models  # noqa: F401

    migrate.init_app(app, db)
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
