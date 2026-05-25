import os


BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "mysql://root:@localhost/test_real_time_chat",
    )
    SOCKETIO_ASYNC_MODE = os.getenv("SOCKETIO_ASYNC_MODE", "threading")
    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USE_SSL = os.getenv("MAIL_USE_SSL", "false").lower() == "true"
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", MAIL_USERNAME or "no-reply@example.com")
    MAIL_SUPPRESS_SEND = os.getenv("MAIL_SUPPRESS_SEND", "false").lower() == "true"
    VERIFICATION_TOKEN_TTL_SECONDS = int(os.getenv("VERIFICATION_TOKEN_TTL_SECONDS", "3600"))
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(25 * 1024 * 1024)))
    MEDIA_UPLOAD_SUBDIR = os.getenv("MEDIA_UPLOAD_SUBDIR", "uploads/chat_media")
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "app", "static", *MEDIA_UPLOAD_SUBDIR.split("/"))
    PROFILE_AVATAR_SUBDIR = os.getenv("PROFILE_AVATAR_SUBDIR", "uploads/profile_avatars")
    PROFILE_AVATAR_UPLOAD_FOLDER = os.path.join(
        BASE_DIR, "app", "static", *PROFILE_AVATAR_SUBDIR.split("/")
    )
    ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov"}
    ADMIN_EMAILS = {
        email.strip().lower()
        for email in os.getenv("ADMIN_EMAILS", "").split(",")
        if email.strip()
    }
    SUPER_ADMIN_EMAILS = {
        email.strip().lower()
        for email in os.getenv("SUPER_ADMIN_EMAILS", "").split(",")
        if email.strip()
    }
