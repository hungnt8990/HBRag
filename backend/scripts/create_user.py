from __future__ import annotations

import argparse
import asyncio
import getpass
import logging

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.organization import Organization
from app.models.user import Role, User

logger = logging.getLogger("create_user")


async def create_user(
    *,
    username: str,
    password: str,
    ma_dviqly: str,
    roles: list[str],
    email: str | None,
    full_name: str | None,
) -> None:
    async with AsyncSessionLocal() as session:
        existing_user = await session.scalar(select(User).where(User.username == username))
        if existing_user is not None:
            raise ValueError(f"User already exists: {username}")

        organization = await session.scalar(
            select(Organization).where(Organization.ma_dviqly == ma_dviqly)
        )
        if organization is None:
            raise ValueError(f"Organization not found: {ma_dviqly}")

        role_result = await session.execute(select(Role).where(Role.name.in_(roles)))
        role_models = list(role_result.scalars())
        found_roles = {role.name for role in role_models}
        missing_roles = set(roles) - found_roles
        if missing_roles:
            raise ValueError(f"Roles not found: {', '.join(sorted(missing_roles))}")

        user = User(
            username=username,
            email=email,
            full_name=full_name,
            hashed_password=hash_password(password),
            organization_id=organization.id,
            is_active=True,
            roles=role_models,
        )
        session.add(user)
        await session.commit()
        logger.info("Created user %s in organization %s", username, ma_dviqly)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an HBRag user.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--ma-dviqly", required=True)
    parser.add_argument("--role", action="append", required=True)
    parser.add_argument("--email")
    parser.add_argument("--full-name")
    parser.add_argument("--password")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    password = args.password or getpass.getpass("Password: ")
    asyncio.run(
        create_user(
            username=args.username,
            password=password,
            ma_dviqly=args.ma_dviqly,
            roles=args.role,
            email=args.email,
            full_name=args.full_name,
        )
    )


if __name__ == "__main__":
    main()
