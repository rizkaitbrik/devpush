from starlette_wtf import StarletteForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, Length, EqualTo

from dependencies import get_lazy_translation as _l


class EmailLoginForm(StarletteForm):
    email = StringField(
        _l("Email address"),
        filters=[lambda v: v.strip() if v else v, lambda v: v.lower() if v else v],
        validators=[
            DataRequired(),
            Email(message=_l("Invalid email address")),
            Length(max=320),
        ],
    )
    submit = SubmitField(_l("Continue with email"))


class SignupForm(StarletteForm):
    email = StringField(
        _l("Email address"),
        filters=[lambda v: v.strip() if v else v, lambda v: v.lower() if v else v],
        validators=[
            DataRequired(),
            Email(message=_l("Invalid email address")),
            Length(max=320),
        ],
    )
    password = PasswordField(
        _l("Password"),
        validators=[
            DataRequired(),
            Length(min=8, max=128, message=_l("Password must be at least 8 characters")),
        ],
    )
    password_confirm = PasswordField(
        _l("Confirm password"),
        validators=[
            DataRequired(),
            EqualTo("password", message=_l("Passwords do not match")),
        ],
    )
    submit = SubmitField(_l("Create account"))


class PasswordLoginForm(StarletteForm):
    email = StringField(
        _l("Email address"),
        filters=[lambda v: v.strip() if v else v, lambda v: v.lower() if v else v],
        validators=[
            DataRequired(),
            Email(message=_l("Invalid email address")),
            Length(max=320),
        ],
    )
    password = PasswordField(
        _l("Password"),
        validators=[DataRequired()],
    )
    submit = SubmitField(_l("Sign in"))
