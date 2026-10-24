from __future__ import annotations

import pytest

from app.services.accounts import AccountError, AccountsService, validate_discord_id
from app.web.sessions import Sessions

USER_ID = 12345678901234567


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
    viewer = await accounts.create(actor=owner, discord_user_id=USER_ID, name="Ana", role="viewer")
    assert await accounts.guilds(viewer.id) == []
    for bad in ("@ana", " 12345678901234567", "01234567890123456", "9" * 20, "1"):
        with pytest.raises(AccountError):
            validate_discord_id(bad)
    with pytest.raises(AccountError):
        await accounts.create(actor=owner, discord_user_id=USER_ID, name="Ana", role="viewer")
    with pytest.raises(AccountError):
        await accounts.create(actor=owner, discord_user_id=USER_ID + 1, name="Extra owner", role="owner")
    with pytest.raises(PermissionError):
        await accounts.create(actor=viewer, discord_user_id=USER_ID + 1, name="Another", role="admin")
    with pytest.raises(AccountError):
        await accounts.set_status(owner.id, "suspended", actor=owner)
    with pytest.raises(AccountError):
        await accounts.update(owner.id, actor=owner, name="Owner", note=None, role="viewer")
    with pytest.raises(AccountError):
        await accounts.set_grants(owner.id, [1], actor=owner)


async def test_grants_survive_leave_rejoin_and_revoke_keeps_history(db, cfg, settings_service):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    admin = await accounts.create(actor=owner, discord_user_id=USER_ID, name="Ana", role="admin", guild_ids=[1])
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
            await accounts.create(actor=owner, discord_user_id=USER_ID, name="Ana", role="admin", guild_ids=[1], conn=conn)
            raise RuntimeError("activity insert failed")
    assert await accounts.by_discord_id(USER_ID) is None
    assert await db.scalar("SELECT COUNT(*) FROM account_guilds") == 0
