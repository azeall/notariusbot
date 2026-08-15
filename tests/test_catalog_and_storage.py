import uuid

import pytest

from app.domain.catalog import list_services, score_service, search_services
from app.domain.security import (
    decrypt_bytes,
    encrypt_bytes,
    hash_password,
    hash_token,
    verify_password,
)
from app.domain.storage import DocumentStorage


async def test_search_finds_service_by_colloquial_word(session, tenant, service, visit_service):
    found = await search_services(session, tenant.id, "нужна доверенность на машину")
    assert found
    assert found[0].id == service.id


async def test_search_matches_by_title(session, tenant, service, visit_service):
    found = await search_services(session, tenant.id, "завещание")
    assert found
    assert found[0].id == visit_service.id


async def test_search_returns_empty_for_nonsense(session, tenant, service):
    assert await search_services(session, tenant.id, "пиццу привезите") == []


async def test_search_is_tenant_isolated(session, tenant, other_tenant, service):
    assert await search_services(session, other_tenant.id, "доверенность") == []


async def test_inactive_service_is_hidden(session, tenant, service):
    service.is_active = False
    await session.commit()
    assert await list_services(session, tenant.id) == []


def test_title_match_outranks_keyword_match(service):
    title_hit = score_service(service, "доверенность")
    keyword_hit = score_service(service, "машина")
    assert title_hit > keyword_hit


def test_password_hash_roundtrip():
    hashed = hash_password("secret123")
    assert hashed != "secret123"
    assert verify_password("secret123", hashed)
    assert not verify_password("secret124", hashed)


def test_token_hash_is_stable_and_opaque():
    token = "abcdef"
    assert hash_token(token) == hash_token(token)
    assert token not in hash_token(token)
    assert len(hash_token(token)) == 64


def test_encryption_roundtrip():
    data = "паспорт 4509 №123456".encode("utf-8")
    blob = encrypt_bytes(data)
    assert blob != data
    assert b"\xd0\xbf\xd0\xb0\xd1\x81" not in blob  # открытого текста в шифре нет
    assert decrypt_bytes(blob) == data


def test_storage_writes_encrypted_and_reads_back(tmp_path):
    storage = DocumentStorage(root=tmp_path)
    tenant_id, request_id = uuid.uuid4(), uuid.uuid4()
    payload = b"%PDF-1.4 fake scan"

    relative = storage.save(tenant_id, request_id, payload)
    on_disk = (tmp_path / relative).read_bytes()

    assert payload not in on_disk, "на диске не должно быть открытого содержимого"
    assert storage.load(relative) == payload


def test_storage_rejects_path_traversal(tmp_path):
    storage = DocumentStorage(root=tmp_path)
    with pytest.raises(ValueError):
        storage.load("../../../../windows/win.ini")


def test_storage_delete_is_idempotent(tmp_path):
    storage = DocumentStorage(root=tmp_path)
    relative = storage.save(uuid.uuid4(), uuid.uuid4(), b"data")
    assert storage.delete(relative) is True
    assert storage.delete(relative) is False
    assert storage.exists(relative) is False
