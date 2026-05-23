from flask_wtf import FlaskForm
from wtforms import PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import EqualTo, InputRequired, Length, Optional, ValidationError, URL

from app.models import User


def validate_simple_email_address(email):
    return "@" in email and "." in email.split("@")[-1]


class RegisterForm(FlaskForm):
    username = StringField("Username", validators=[InputRequired(), Length(min=3, max=80)])
    email = StringField("Email", validators=[InputRequired(), Length(max=255)])
    password = PasswordField("Password", validators=[InputRequired(), Length(min=6, max=128)])
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[InputRequired(), EqualTo("password", message="Passwords must match.")],
    )
    submit = SubmitField("Create account")

    def validate_username(self, field):
        if User.query.filter_by(username=field.data.strip()).first():
            raise ValidationError("That username is already in use.")

    def validate_email(self, field):
        email = field.data.strip().lower()
        if not validate_simple_email_address(email):
            raise ValidationError("Enter a valid email address.")

        if User.query.filter_by(email=email).first():
            raise ValidationError("That email is already registered.")


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[InputRequired(), Length(max=255)])
    password = PasswordField("Password", validators=[InputRequired(), Length(min=6, max=128)])
    submit = SubmitField("Sign in")

    def validate_email(self, field):
        if not validate_simple_email_address(field.data.strip().lower()):
            raise ValidationError("Enter a valid email address.")


class RoomForm(FlaskForm):
    name = StringField("Room Name", validators=[InputRequired(), Length(min=2, max=120)])
    submit = SubmitField("Create room")


class PrivateChatForm(FlaskForm):
    username = StringField("Username", validators=[InputRequired(), Length(min=3, max=80)])
    submit = SubmitField("Start chat")


class UserProfileForm(FlaskForm):
    display_name = StringField("Display Name", validators=[Optional(), Length(max=120)])
    bio = TextAreaField("Bio", validators=[Optional(), Length(max=500)])
    avatar_url = StringField("Avatar URL", validators=[Optional(), URL(message="Enter a valid URL."), Length(max=255)])
    submit = SubmitField("Save profile")


class PrivateRoomForm(FlaskForm):
    name = StringField("Private Room Name", validators=[InputRequired(), Length(min=2, max=120)])
    submit = SubmitField("Create private room")


class RoomInviteForm(FlaskForm):
    username = StringField("Invite Username", validators=[InputRequired(), Length(min=3, max=80)])
    submit = SubmitField("Invite")


class ActionForm(FlaskForm):
    submit = SubmitField("Confirm")


class ResendVerificationForm(FlaskForm):
    email = StringField("Email", validators=[InputRequired(), Length(max=255)])
    submit = SubmitField("Resend verification email")

    def validate_email(self, field):
        if not validate_simple_email_address(field.data.strip().lower()):
            raise ValidationError("Enter a valid email address.")


class VerifyEmailForm(FlaskForm):
    email = StringField("Email", validators=[InputRequired(), Length(max=255)])
    code = StringField("Verification Code", validators=[InputRequired(), Length(min=6, max=6)])
    submit = SubmitField("Verify email")

    def validate_email(self, field):
        if not validate_simple_email_address(field.data.strip().lower()):
            raise ValidationError("Enter a valid email address.")

    def validate_code(self, field):
        code = field.data.strip()
        if not code.isdigit() or len(code) != 6:
            raise ValidationError("Enter the 6-digit verification code.")


class AdminUserForm(FlaskForm):
    username = StringField("Username", validators=[InputRequired(), Length(min=3, max=80)])
    email = StringField("Email", validators=[InputRequired(), Length(max=255)])
    password = PasswordField("Password", validators=[Optional(), Length(min=6, max=128)])
    role = SelectField(
        "Role",
        choices=[
            ("user", "User"),
            ("admin", "Admin"),
            ("super_admin", "Super Admin"),
        ],
        validators=[InputRequired()],
    )
    is_email_verified = SelectField(
        "Email Verification",
        choices=[("true", "Verified"), ("false", "Pending")],
        validators=[InputRequired()],
    )
    submit = SubmitField("Save user")

    def __init__(self, original_user=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_user = original_user

    def validate_username(self, field):
        username = field.data.strip()
        existing = User.query.filter_by(username=username).first()
        if existing and (self.original_user is None or existing.id != self.original_user.id):
            raise ValidationError("That username is already in use.")

    def validate_email(self, field):
        email = field.data.strip().lower()
        if not validate_simple_email_address(email):
            raise ValidationError("Enter a valid email address.")

        existing = User.query.filter_by(email=email).first()
        if existing and (self.original_user is None or existing.id != self.original_user.id):
            raise ValidationError("That email is already registered.")
