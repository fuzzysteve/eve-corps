from flask import Flask

from .config import Config
from .extensions import db, migrate


def create_app(config: type[Config] | Config = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config)

    db.init_app(app)
    migrate.init_app(app, db)

    from . import models  # noqa: F401  (register models with the metadata)
    from .auth import bp as auth_bp
    from .moderation import bp as moderation_bp
    from .moderation import open_report_count
    from .views import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(moderation_bp)
    app.jinja_env.globals["open_report_count"] = open_report_count

    from .auth import check_csrf, csrf_token, is_moderator, is_recruiter, load_current_character

    app.before_request(check_csrf)
    app.before_request(load_current_character)
    app.jinja_env.globals.update(csrf_token=csrf_token, is_recruiter=is_recruiter, is_moderator=is_moderator)

    from . import choices
    from .formatting import plain_text, render_markdown

    app.jinja_env.globals["choices"] = choices
    app.jinja_env.filters["markdown"] = render_markdown
    app.jinja_env.filters["excerpt"] = plain_text
    app.after_request(security_headers)

    return app


# Defence in depth for user-written adverts: even if something got past the
# Markdown renderer and sanitizer, the browser won't load or run it.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://cdn.jsdelivr.net",
    "style-src 'self' https://cdn.jsdelivr.net",
    # data: is for Bootstrap's inline SVG icons (checkboxes, select arrows).
    "img-src 'self' data: https://images.evetech.net https://web.ccpgamescdn.com",
    "font-src 'self' https://cdn.jsdelivr.net",
    "connect-src 'self'",
    "object-src 'none'",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self'",
])


def security_headers(response):
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response
