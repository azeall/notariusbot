"""Downloadable iCalendar snapshot, without client data or bearer links."""

from datetime import UTC, datetime

from app.models import Appointment, Tenant


def _text(value: str) -> str:
    return value.replace('\\', '\\\\').replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def _fold(line: str) -> str:
    # RFC 5545: fold at 75 octets, never split a UTF-8 character.
    parts, current, size = [], '', 0
    for char in line:
        length = len(char.encode('utf-8'))
        if size + length > 75:
            parts.append(current)
            current, size = ' ', 1
        current += char
        size += length
    return '\r\n'.join([*parts, current])


def appointment_calendar(appointment: Appointment, tenant: Tenant) -> str:
    def utc(moment: datetime) -> str:
        return moment.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')

    lines = [
        'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Notarybot//Appointments//RU',
        'CALSCALE:GREGORIAN', 'BEGIN:VEVENT',
        f'UID:{appointment.request_id}@notarybot',
        f'DTSTAMP:{utc(datetime.now(UTC))}',
        f'DTSTART:{utc(appointment.starts_at)}', f'DTEND:{utc(appointment.ends_at)}',
        'SUMMARY:' + _text('Приём у нотариуса'),
        'LOCATION:' + _text(tenant.address or tenant.city),
        'DESCRIPTION:' + _text('Сохранённая копия записи. После переноса или отмены обновите событие в календаре вручную.'),
        'END:VEVENT', 'END:VCALENDAR',
    ]
    return '\r\n'.join(_fold(line) for line in lines) + '\r\n'
