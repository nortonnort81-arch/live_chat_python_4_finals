import smtplib
from email.message import EmailMessage

from flask import current_app


class EmailDeliveryError(RuntimeError):
    """Raised when verification email cannot be sent."""


def send_verification_email(user, code):
    subject = "Verify your email address"
    body = (
        f"Hello {user.username},\n\n"
        "Welcome to Real-Time Chat.\n"
        "Please verify your email address before signing in.\n\n"
        f"Your verification code is: {code}\n\n"
        "Enter this 6-digit code on the verification page to activate your account.\n\n"
        "If you did not create this account, you can ignore this email."
    )

    if current_app.config["MAIL_SUPPRESS_SEND"]:
        current_app.logger.info("Verification email suppressed for %s: %s", user.email, code)
        return code

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = current_app.config["MAIL_DEFAULT_SENDER"]
    message["To"] = user.email
    message.set_content(body)

    try:
        if current_app.config["MAIL_USE_SSL"]:
            with smtplib.SMTP_SSL(
                current_app.config["MAIL_SERVER"],
                current_app.config["MAIL_PORT"],
                timeout=30,
            ) as server:
                server.ehlo()
                authenticate_and_send(server, message)
        else:
            with smtplib.SMTP(
                current_app.config["MAIL_SERVER"],
                current_app.config["MAIL_PORT"],
                timeout=30,
            ) as server:
                server.ehlo()
                if current_app.config["MAIL_USE_TLS"]:
                    server.starttls()
                    server.ehlo()
                authenticate_and_send(server, message)
    except smtplib.SMTPException as exc:
        raise EmailDeliveryError(
            "Could not send verification email. Check MAIL_* settings in .env."
        ) from exc

    return code


def send_password_reset_email(user, code):
    subject = "Reset your password"
    body = (
        f"Hello {user.username},\n\n"
        "We received a request to reset the password for your Real-Time Chat account.\n\n"
        f"Your password reset code is: {code}\n\n"
        "Enter this 6-digit code on the reset password page. "
        "The code expires in one hour.\n\n"
        "If you did not request a password reset, you can ignore this email."
    )

    if current_app.config["MAIL_SUPPRESS_SEND"]:
        current_app.logger.info("Password reset email suppressed for %s: %s", user.email, code)
        return code

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = current_app.config["MAIL_DEFAULT_SENDER"]
    message["To"] = user.email
    message.set_content(body)

    try:
        if current_app.config["MAIL_USE_SSL"]:
            with smtplib.SMTP_SSL(
                current_app.config["MAIL_SERVER"],
                current_app.config["MAIL_PORT"],
                timeout=30,
            ) as server:
                server.ehlo()
                authenticate_and_send(server, message)
        else:
            with smtplib.SMTP(
                current_app.config["MAIL_SERVER"],
                current_app.config["MAIL_PORT"],
                timeout=30,
            ) as server:
                server.ehlo()
                if current_app.config["MAIL_USE_TLS"]:
                    server.starttls()
                    server.ehlo()
                authenticate_and_send(server, message)
    except smtplib.SMTPException as exc:
        raise EmailDeliveryError(
            "Could not send password reset email. Check MAIL_* settings in .env."
        ) from exc

    return code


def authenticate_and_send(server, message):
    username = (current_app.config["MAIL_USERNAME"] or "").strip()
    password = current_app.config["MAIL_PASSWORD"] or ""

    if not username or not password:
        raise EmailDeliveryError(
            "MAIL_USERNAME and MAIL_PASSWORD are missing. "
            "For local testing, set MAIL_SUPPRESS_SEND=true in .env."
        )

    server.login(username, password)
    server.send_message(message)
