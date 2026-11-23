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
    from .views import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)

    from .auth import check_csrf, csrf_token, is_recruiter, load_current_character

    app.before_request(check_csrf)
    app.before_request(load_current_character)
    app.jinja_env.globals.update(csrf_token=csrf_token, is_recruiter=is_recruiter)

    from . import choices

    app.jinja_env.globals["choices"] = choices

    return app
