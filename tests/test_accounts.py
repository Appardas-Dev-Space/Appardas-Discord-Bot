from __future__ import annotations

import sqlite3

import pytest

from app.services.accounts import AccountError, AccountsService, validate_discord_id, verify_password
from app.util import iso
from app.web.sessions import Sessions

USER_ID = 12345678901234567
TEMPORARY_PASSWORD = "Temporary-pass123!"


async def test_owner_bootstrap_is_authoritative_and_preserves_labels(db, cfg):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    assert owner.role == "owner" and owner.is_system
    await accounts.update(owner.id, actor=owner, name="Operator", note="Keep this")
    raw = await Sessions(db, cfg).create(owner)
    await db.close()
    await db.connect(owner_ids=(2,))
    removed = await accounts.by_discord_id(1)
    assert removed.role == "viewer" and not removed.is_system
    assert removed.name == "Operator" and removed.note == "Keep this"
    assert await Sessions(db, cfg).lookup(raw) is None
    assert (await accounts.by_discord_id(2)).role == "owner"
    await db.execute("UPDATE accounts SET status = 'suspended' WHERE discord_user_id = 2")
    await db.close()
    await db.connect(owner_ids=(2,))
    assert (await accounts.by_discord_id(2)).status == "active"


async def test_closed_accounts_validation_and_system_owner_guards(db, settings_service):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    viewer = await accounts.create(actor=owner, discord_user_id=USER_ID, username="ana", temporary_password=TEMPORARY_PASSWORD, name="Ana", role="viewer")
    assert await accounts.guilds(viewer.id) == []
    for bad in ("@ana", " 12345678901234567", "01234567890123456", "9" * 20, "1"):
        with pytest.raises(AccountError):
            validate_discord_id(bad)
    with pytest.raises(AccountError):
        await accounts.create(actor=owner, discord_user_id=USER_ID, username="ana-again", temporary_password=TEMPORARY_PASSWORD, name="Ana", role="viewer")
    with pytest.raises(AccountError):
        await accounts.create(actor=owner, discord_user_id=USER_ID + 1, username="extra-owner", temporary_password=TEMPORARY_PASSWORD, name="Extra owner", role="owner")
    with pytest.raises(PermissionError):
        await accounts.create(actor=viewer, discord_user_id=USER_ID + 1, username="another", temporary_password=TEMPORARY_PASSWORD, name="Another", role="admin")
    with pytest.raises(AccountError):
        await accounts.set_status(owner.id, "suspended", actor=owner)
    with pytest.raises(AccountError):
        await accounts.update(owner.id, actor=owner, name="Owner", note=None, role="viewer")
    with pytest.raises(AccountError):
        await accounts.set_grants(owner.id, [1], actor=owner)


async def test_grants_survive_leave_rejoin_and_revoke_keeps_history(db, cfg, settings_service):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    admin = await accounts.create(actor=owner, discord_user_id=USER_ID, username="ana", temporary_password=TEMPORARY_PASSWORD, name="Ana", role="admin", guild_ids=[1])
    assert await accounts.has_grant(admin, 1)
    raw = await Sessions(db, cfg).create(admin)
    await settings_service.forget_guild(1)
    assert (await accounts.guilds(admin.id))[0]["left_at"]
    await settings_service.ensure_guild(1, "Returned server")
    assert (await accounts.guilds(admin.id))[0]["left_at"] is None
    revoked, deleted = await accounts.set_status(admin.id, "revoked", actor=owner)
    assert deleted == 1 and revoked.status == "revoked"
    assert await Sessions(db, cfg).lookup(raw) is None
    assert len(await accounts.guilds(admin.id)) == 1
    await accounts.set_status(admin.id, "active", actor=owner)
    assert await accounts.set_grants(admin.id, [], actor=owner) == (set(), {1})
    assert not await accounts.has_grant(admin, 1)
    assert await accounts.set_grants(admin.id, [1], actor=owner) == ({1}, set())
    await db.execute("DELETE FROM guilds WHERE id = 1")
    assert await accounts.guilds(admin.id) == []


async def test_account_and_grants_roll_back_as_one(db, settings_service):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    with pytest.raises(RuntimeError):
        async with db.transaction() as conn:
            await accounts.create(actor=owner, discord_user_id=USER_ID, username="ana", temporary_password=TEMPORARY_PASSWORD, name="Ana", role="admin", guild_ids=[1], conn=conn)
            raise RuntimeError("activity insert failed")
    assert await accounts.by_discord_id(USER_ID) is None
    assert await db.scalar("SELECT COUNT(*) FROM account_guilds") == 0


async def test_created_account_has_local_credentials_and_exact_discord_id(db, settings_service):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    account = await accounts.create(
        actor=owner, discord_user_id=str(USER_ID), username=" Ana ", temporary_password=TEMPORARY_PASSWORD,
        name="Ana", role="viewer", guild_ids=[1],
    )
    assert account.username == "ana" and account.must_change_password
    assert account.discord_user_id == USER_ID
    row = await db.fetchone("SELECT *, typeof(discord_user_id) AS discord_type FROM accounts WHERE id = ?", (account.id,))
    assert row["discord_type"] == "text" and row["discord_user_id"] == str(USER_ID)
    assert verify_password(TEMPORARY_PASSWORD, row["password_hash"])
    assert TEMPORARY_PASSWORD not in str(row) and TEMPORARY_PASSWORD not in str(account)
    assert (await accounts.authenticate(" ANA ", TEMPORARY_PASSWORD)).id == account.id
    assert await accounts.authenticate(str(USER_ID), TEMPORARY_PASSWORD) is None
    assert (await accounts.by_discord_id(str(USER_ID))).id == account.id
    assert [row["id"] for row in await accounts.guilds(account.id)] == [1]
    assert [row["id"] for row in await accounts.list(q="ANA")] == [account.id]


@pytest.mark.parametrize("field,value", [
    ("discord_user_id", ""),
    ("username", ""), ("username", "  "), ("username", "ana smith"), ("username", "ána"), ("username", "a" * 65),
    ("temporary_password", ""), ("temporary_password", "12345"), ("temporary_password", "      "),
])
async def test_creation_rejects_invalid_required_credentials_atomically(db, settings_service, field, value):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    count = await db.scalar("SELECT COUNT(*) FROM accounts")
    values = dict(actor=owner, discord_user_id=str(USER_ID), username="ana", temporary_password=TEMPORARY_PASSWORD, name="Ana", role="viewer", guild_ids=[1])
    values[field] = value
    with pytest.raises(AccountError) as excinfo:
        await accounts.create(**values)
    assert excinfo.value.field == field
    assert await db.scalar("SELECT COUNT(*) FROM accounts") == count
    assert await db.scalar("SELECT COUNT(*) FROM account_guilds") == 0


async def test_username_uniqueness_matches_case_insensitive_login(db, settings_service):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    account = await accounts.create(actor=owner, discord_user_id=USER_ID, username="ana", temporary_password=TEMPORARY_PASSWORD, name="Ana", role="viewer", guild_ids=[1])
    with pytest.raises(AccountError) as excinfo:
        await accounts.create(actor=owner, discord_user_id=USER_ID + 1, username=" ANA ", temporary_password=TEMPORARY_PASSWORD, name="Another", role="viewer", guild_ids=[1])
    assert excinfo.value.field == "username"
    assert await accounts.by_discord_id(USER_ID + 1) is None
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute("UPDATE accounts SET username = 'ANA' WHERE id = ?", (owner.id,))
    assert (await accounts.by_username("ANA")).id == account.id
    assert await db.scalar("SELECT COUNT(*) FROM account_guilds") == 1


async def test_assign_credentials_preserves_existing_discord_only_account(db, settings_service):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    now = iso()
    cursor = await db.execute(
        "INSERT INTO accounts (discord_user_id, name, role, status, note, created_by, created_at, updated_at) VALUES (?, 'Legacy Ana', 'admin', 'suspended', 'Keep history', ?, ?, ?)",
        (str(USER_ID), owner.id, now, now),
    )
    account_id = cursor.lastrowid
    await accounts.set_grants(account_id, [1], actor=owner)
    before = await db.fetchone("SELECT * FROM accounts WHERE id = ?", (account_id,))
    updated, deleted = await accounts.set_credentials(account_id, actor=owner, username="ana", temporary_password=TEMPORARY_PASSWORD)
    after = await db.fetchone("SELECT * FROM accounts WHERE id = ?", (account_id,))
    for key in before.keys() - {"username", "password_hash", "must_change_password", "updated_at"}:
        assert before[key] == after[key]
    assert updated.id == account_id and updated.status == "suspended" and deleted == 0
    assert await accounts.authenticate("ana", TEMPORARY_PASSWORD) is None
    await accounts.set_status(account_id, "active", actor=owner)
    assert (await accounts.authenticate("ana", TEMPORARY_PASSWORD)).must_change_password
    changed = await accounts.change_password(account_id, TEMPORARY_PASSWORD, "Permanent-pass123!")
    assert not changed.must_change_password and changed.discord_user_id == USER_ID and changed.username == "ana"
    assert await accounts.authenticate("ana", TEMPORARY_PASSWORD) is None
    assert (await accounts.authenticate("ana", "Permanent-pass123!")).id == account_id
    assert [row["id"] for row in await accounts.guilds(account_id)] == [1]


async def test_reset_credentials_invalidates_sessions_and_old_password(db, cfg, settings_service):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    account = await accounts.create(actor=owner, discord_user_id=USER_ID, username="ana", temporary_password=TEMPORARY_PASSWORD, name="Ana", role="admin", guild_ids=[1])
    account = await accounts.change_password(account.id, TEMPORARY_PASSWORD, "Permanent-pass123!")
    raw = await Sessions(db, cfg).create(account)
    updated, deleted = await accounts.set_credentials(account.id, actor=owner, username="ana-new", temporary_password="Reset-pass123!")
    assert deleted == 1 and updated.must_change_password
    assert updated.discord_user_id == USER_ID and updated.role == "admin"
    assert await Sessions(db, cfg).lookup(raw) is None
    assert await accounts.authenticate("ana", "Permanent-pass123!") is None
    assert await accounts.authenticate("ana-new", "Permanent-pass123!") is None
    assert (await accounts.authenticate("ana-new", "Reset-pass123!")).id == account.id
    assert [row["id"] for row in await accounts.guilds(account.id)] == [1]


async def test_credential_reset_rechecks_owner_permissions(db):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    viewer = await accounts.create(actor=owner, discord_user_id=USER_ID, username="ana", temporary_password=TEMPORARY_PASSWORD, name="Ana", role="viewer")
    with pytest.raises(PermissionError):
        await accounts.set_credentials(viewer.id, actor=viewer, username="ana", temporary_password="Reset-pass123!")
    await db.execute("UPDATE accounts SET role = 'admin' WHERE id = ?", (owner.id,))
    with pytest.raises(PermissionError):
        await accounts.set_credentials(viewer.id, actor=owner, username="ana", temporary_password="Reset-pass123!")
    assert (await accounts.authenticate("ana", TEMPORARY_PASSWORD)).id == viewer.id


async def test_credential_reset_and_session_deletion_roll_back_together(db, cfg):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    account = await accounts.create(actor=owner, discord_user_id=USER_ID, username="ana", temporary_password=TEMPORARY_PASSWORD, name="Ana", role="viewer")
    raw = await Sessions(db, cfg).create(account)
    before = await db.fetchone("SELECT * FROM accounts WHERE id = ?", (account.id,))
    with pytest.raises(RuntimeError):
        async with db.transaction() as conn:
            await accounts.set_credentials(account.id, actor=owner, username="ana-new", temporary_password="Reset-pass123!", conn=conn)
            raise RuntimeError("activity insert failed")
    assert await db.fetchone("SELECT * FROM accounts WHERE id = ?", (account.id,)) == before
    assert await Sessions(db, cfg).lookup(raw) is not None


async def test_system_credential_assignment_preserves_bootstrap_identity(db, cfg):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    preliminary = await accounts.by_username("appardas-admin")
    with pytest.raises(AccountError) as excinfo:
        await accounts.set_credentials(preliminary.id, actor=owner, username="renamed-admin", temporary_password=TEMPORARY_PASSWORD)
    assert excinfo.value.field == "username"
    updated, _ = await accounts.set_credentials(owner.id, actor=owner, username="bot-owner", temporary_password=TEMPORARY_PASSWORD)
    assert updated.is_system and updated.role == "owner"
    count = await db.scalar("SELECT COUNT(*) FROM accounts")
    await db.close()
    await db.connect(owner_ids=cfg.owner_ids)
    assert await db.scalar("SELECT COUNT(*) FROM accounts") == count
    assert (await accounts.authenticate("bot-owner", TEMPORARY_PASSWORD)).id == owner.id
    assert (await accounts.by_username("appardas-admin")).id == preliminary.id
