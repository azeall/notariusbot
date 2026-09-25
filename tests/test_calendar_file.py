from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.domain.calendar_file import appointment_calendar


def test_calendar_escapes_text_and_folds_utf8_without_property_injection():
    start = datetime(2026, 10, 1, 10, tzinfo=UTC)
    appointment = SimpleNamespace(request_id=uuid4(), starts_at=start, ends_at=start + timedelta(hours=1))
    tenant = SimpleNamespace(address='Москва, ' + 'Очень длинная улица; ' * 12 + '\r\nATTENDEE:private@example.test\\office', city='')
    text = appointment_calendar(appointment, tenant)
    assert text.endswith('END:VCALENDAR\r\n')
    assert all(len(line.encode('utf-8')) <= 75 for line in text.split('\r\n'))
    unfolded = text.replace('\r\n ', '')
    assert '\r\nATTENDEE:' not in unfolded
    assert 'Москва\\,' in unfolded
    assert 'улица\\;' in unfolded
    assert '\\nATTENDEE:' in unfolded
    assert '\\\\office' in unfolded
    assert 'DTEND:20261001T110000Z' in unfolded
