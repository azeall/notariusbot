from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256

from app import legal
from app.models import Client, Tenant


def consent_fingerprint(tenant: Tenant) -> str:
    return sha256(legal.consent_text(tenant).encode("utf-8")).hexdigest()


def record_consent(client: Client, tenant: Tenant) -> None:
    """Зафиксировать текст и реквизиты на момент принятия согласия."""
    now = datetime.now(UTC)
    client.consent_given_at = now
    client.consent_text_version = legal.CONSENT_VERSION
    client.consent_receipt = {
        "version": legal.CONSENT_VERSION,
        "text": legal.consent_text(tenant),
        "operator": asdict(legal.Operator.of(tenant)),
        "accepted_at": now.isoformat(),
        "channel": client.channel.value,
    }
