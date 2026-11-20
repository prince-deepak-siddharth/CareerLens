import argparse
import getpass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .auth import Credentials, passwords
from .config import settings
from .db import engine
from .models import Conversation, Interaction, Resume, User


def create_admin(email, password):
    credentials = Credentials(email=email, password=password)
    if credentials.email not in settings().admins:
        raise ValueError("Add the address to CAREERLENS_ADMINS first")
    with Session(engine) as db:
        if db.scalar(select(User.id).where(User.email == credentials.email)):
            raise ValueError("This address belongs to an existing account")
        db.add(
            User(email=credentials.email, password_hash=passwords.hash(credentials.password), is_admin=True)
        )
        db.commit()


def prune():
    now = datetime.now(UTC)
    with Session(engine) as db:
        deletes = {
            "resume_drafts": delete(Resume).where(
                Resume.confirmed.is_(False), Resume.created_at < now - timedelta(days=7)
            ),
            "conversations": delete(Conversation).where(Conversation.updated_at < now - timedelta(days=180)),
            "interactions": delete(Interaction).where(Interaction.created_at < now - timedelta(days=180)),
        }
        counts = {name: db.execute(query).rowcount for name, query in deletes.items()}
        db.commit()
        print(counts)


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    admin = commands.add_parser("create-admin")
    admin.add_argument("--email", required=True)
    commands.add_parser("prune")
    args = parser.parse_args()
    if args.command == "prune":
        prune()
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Confirm password: ") != password:
            parser.error("Passwords must match")
        create_admin(args.email, password)
        print("Administrator created")


if __name__ == "__main__":
    main()
