from urllib.parse import unquote, urlparse

import MySQLdb
from MySQLdb.cursors import DictCursor
from flask import current_app, g


def parse_database_url(url):
    if not url:
        raise ValueError("DATABASE_URL is not configured.")

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").split("+", 1)[0].lower()
    if scheme not in {"mysql", "mariadb"}:
        raise ValueError(f"Unsupported database scheme: {parsed.scheme}")

    database = (parsed.path or "").lstrip("/")
    if not database:
        raise ValueError("DATABASE_URL must include a database name.")

    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": database,
        "charset": "utf8mb4",
    }


def get_db():
    if "db_conn" not in g:
        settings = parse_database_url(current_app.config["DATABASE_URL"])
        g.db_conn = MySQLdb.connect(
            host=settings["host"],
            port=settings["port"],
            user=settings["user"],
            passwd=settings["password"],
            db=settings["database"],
            charset=settings["charset"],
            cursorclass=DictCursor,
            autocommit=False,
        )
    return g.db_conn


def get_cursor():
    return get_db().cursor()


def close_db(_exc=None):
    connection = g.pop("db_conn", None)
    if connection is not None:
        connection.close()


def commit():
    get_db().commit()


def rollback():
    get_db().rollback()


def execute(sql, params=None):
    cursor = get_cursor()
    cursor.execute(sql, params or ())
    return cursor


def fetchone(sql, params=None):
    cursor = execute(sql, params)
    return cursor.fetchone()


def fetchall(sql, params=None):
    cursor = execute(sql, params)
    return cursor.fetchall()


def last_insert_id():
    return get_db().insert_id()
