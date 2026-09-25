"""Public, printable preparation checklist from the tenant's current catalog."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request as HttpRequest
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.theme import build_palette, palette_css
from app.models import Service, ServiceDocument, Tenant
from app.web.deps import db_session, resolve_tenant

router = APIRouter(tags=["pages"])


@router.get("/{slug}/services/{service_id}/prepare", response_class=HTMLResponse,
            name="preparation_page")
async def preparation_page(
    http_request: HttpRequest,
    service_id: str,
    tenant: Tenant = Depends(resolve_tenant),
    session: AsyncSession = Depends(db_session),
):
    from app.web.main import TEMPLATES

    try:
        identifier = uuid.UUID(service_id)
    except ValueError:
        raise HTTPException(404, "Услуга не найдена") from None

    service = await session.scalar(
        select(Service)
        .where(Service.id == identifier, Service.tenant_id == tenant.id,
               Service.is_active.is_(True))
        .options(selectinload(Service.documents.and_(ServiceDocument.tenant_id == tenant.id)))
    )
    if service is None:
        raise HTTPException(404, "Услуга не найдена")

    palette = build_palette(tenant.widget_mode, tenant.widget_accent, tenant.widget_font)
    return TEMPLATES.TemplateResponse(
        http_request,
        "preparation.html",
        {
            "title": f"Подготовка — {service.title}",
            "tenant": tenant,
            "service": service,
            "documents": sorted(
                (doc for doc in service.documents if doc.tenant_id == tenant.id),
                key=lambda doc: doc.sort_order,
            ),
            "palette_css": palette_css(palette),
        },
        headers={"Cache-Control": "no-store"},
    )
