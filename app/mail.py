import smtplib
from email.message import EmailMessage

from flask import current_app


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

    if current_app.config["MAIL_USE_SSL"]:
        with smtplib.SMTP_SSL(
            current_app.config["MAIL_SERVER"],
            current_app.config["MAIL_PORT"],
        ) as server:
            authenticate_and_send(server, message)
    else:
        with smtplib.SMTP(
            current_app.config["MAIL_SERVER"],
            current_app.config["MAIL_PORT"],
        ) as server:
            if current_app.config["MAIL_USE_TLS"]:
                server.starttls()
            authenticate_and_send(server, message)

    return code


def authenticate_and_send(server, message):
    username = current_app.config["MAIL_USERNAME"]
    password = current_app.config["MAIL_PASSWORD"]

    if username:
        server.login(username, password)

    server.send_message(message)
