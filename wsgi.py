from werkzeug.middleware.proxy_fix import ProxyFix

from evecorps import create_app

app = create_app()
# Apache terminates TLS and proxies to gunicorn on localhost.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
