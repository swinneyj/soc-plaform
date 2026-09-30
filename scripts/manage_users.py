#!/usr/bin/env python3
"""Manage operator accounts for session auth (SESSION_AUTH_PLAN.md Piece A).

Stdlib-only CLI on top of api.auth.hash_password. Reads DATABASE_URL exactly
like the API does, so run it with the same environment you run the platform
with, e.g. `bws run -- python scripts/manage_users.py list`.

Subcommands:
  create <username> --role analyst|admin   (--password-env VARNAME or prompt)
  list
  set-role <username> <analyst|admin>
  set-password <username>                  (--password-env VARNAME or prompt)
  deactivate <username>

Secrets: the password comes from a getpass prompt or from the env var NAMED
by --password-env — never from argv, never printed.
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.models import SessionLocal, User  # noqa: E402
from api.auth import hash_password  # noqa: E402


def _ensure_schema():
    """Idempotent create-if-missing — the same Base.metadata.create_all call
    the API lifespan makes on startup — so this CLI can provision the first
    admin on a fresh database."""
    from db.models import Base, engine

    Base.metadata.create_all(bind=engine)


def _resolve_password(args) -> str:
    if args.password_env:
        value = os.environ.get(args.password_env, "")
        if not value:
            print(f"error: env var {args.password_env!r} is not set", file=sys.stderr)
            sys.exit(2)
        return value
    pw = getpass.getpass("password: ")
    confirm = getpass.getpass("confirm:  ")
    if not pw:
        print("error: empty password", file=sys.stderr)
        sys.exit(2)
    if pw != confirm:
        print("error: passwords do not match", file=sys.stderr)
        sys.exit(2)
    return pw


def _get_user(db, username):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        print(f"error: no such user: {username}", file=sys.stderr)
        sys.exit(1)
    return user


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="create a user")
    p_create.add_argument("username")
    p_create.add_argument("--role", choices=("analyst", "admin"), default="analyst")
    p_create.add_argument("--password-env")

    sub.add_parser("list", help="list users")

    p_role = sub.add_parser("set-role", help="change a user's role")
    p_role.add_argument("username")
    p_role.add_argument("role", choices=("analyst", "admin"))

    p_pw = sub.add_parser("set-password", help="set a user's password")
    p_pw.add_argument("username")
    p_pw.add_argument("--password-env")

    p_deact = sub.add_parser("deactivate", help="deactivate a user")
    p_deact.add_argument("username")

    args = parser.parse_args()
    _ensure_schema()
    db = SessionLocal()
    try:
        if args.command == "create":
            if db.query(User).filter(User.username == args.username).first():
                print(f"error: user already exists: {args.username}", file=sys.stderr)
                sys.exit(1)
            db.add(User(
                username=args.username,
                role=args.role,
                password_hash=hash_password(_resolve_password(args)),
            ))
            db.commit()
            print(f"created user {args.username} (role={args.role})")
        elif args.command == "list":
            for u in db.query(User).order_by(User.id).all():
                state = "active" if u.is_active else "deactivated"
                print(f"{u.id:>4}  {u.username:<32} {u.role:<8} {state}")
        elif args.command == "set-role":
            user = _get_user(db, args.username)
            user.role = args.role
            db.commit()
            print(f"{args.username}: role set to {args.role}")
        elif args.command == "set-password":
            user = _get_user(db, args.username)
            user.password_hash = hash_password(_resolve_password(args))
            db.commit()
            print(f"{args.username}: password updated")
        elif args.command == "deactivate":
            user = _get_user(db, args.username)
            user.is_active = False
            db.commit()
            print(f"{args.username}: deactivated")
    finally:
        db.close()


if __name__ == "__main__":
    main()
