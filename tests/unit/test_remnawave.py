import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest

from app.domain.dto.vpn_user import VpnSubscription
from app.domain.errors.vpn_user import PanelUnavailableError

pytestmark = pytest.mark.unit
GUID = UUID('00000000-0000-0000-0000-000000000123')
EXPIRES = datetime(2026, 11, 1, tzinfo=UTC)


def panel_response(**overrides):
    data = {
        'id': 17, 'username': 'vpn_' + GUID.hex, 'description': 'asyncpytemplate:' + str(GUID),
        'subscriptionUrl': 'https://subscription.invalid/key', 'expireAt': '2026-11-01T00:00:00Z',
        'trojanPassword': 'not-for-local-storage', 'ssPassword': 'not-for-local-storage',
        'status': 'ACTIVE', 'trafficLimitBytes': 0, 'trafficLimitStrategy': 'NO_RESET',
        'shortUuid': 'short', 'email': None, 'telegramId': None, 'tag': None,
        'activeInternalSquads': [], 'externalSquadUuid': None, 'hwidDeviceLimit': None,
        'vlessUuid': str(GUID), 'lastTriggeredThreshold': 0, 'subRevokedAt': None,
        'lastTrafficResetAt': None, 'createdAt': '2026-10-25T00:00:00Z',
        'updatedAt': '2026-10-25T00:00:00Z',
    }
    data.update(overrides)
    return {'response': data}


async def test_create_uses_201_and_stable_owner_marker():
    from app.infrastructure.adapters.vpn.remnawave import RemnawavePanel
    requests = []
    async def handler(request):
        requests.append(request)
        if request.method == 'GET':
            return httpx.Response(404)
        body = json.loads(request.content)
        assert body == {'username': 'vpn_' + GUID.hex, 'description': 'asyncpytemplate:' + str(GUID),
                        'expireAt': '2026-11-01T00:00:00Z', 'status': 'ACTIVE',
                        'trafficLimitBytes': 0, 'activeInternalSquads': []}
        return httpx.Response(201, json=panel_response())
    async with httpx.AsyncClient(base_url='https://panel.invalid', transport=httpx.MockTransport(handler)) as client:
        result = await RemnawavePanel(client).create(VpnSubscription(user_guid=GUID), EXPIRES)
    assert result.external_id == 17 and result.expires_at == EXPIRES
    assert [r.url.path for r in requests] == ['/api/users/by-username/vpn_' + GUID.hex, '/api/users']


async def test_create_timeout_reconciles_existing_owner_and_preserves_real_expiry():
    from app.infrastructure.adapters.vpn.remnawave import RemnawavePanel
    created = False
    async def handler(request):
        nonlocal created
        if request.method == 'GET':
            return httpx.Response(200, json=panel_response()) if created else httpx.Response(404)
        created = True
        raise httpx.ReadTimeout('secret body and token', request=request)
    async with httpx.AsyncClient(base_url='https://panel.invalid', transport=httpx.MockTransport(handler)) as client:
        panel = RemnawavePanel(client)
        result = await panel.create(VpnSubscription(user_guid=GUID), EXPIRES + timedelta(days=1))
    assert result.expires_at == EXPIRES


async def test_never_adopts_username_collision_without_owner_marker():
    from app.infrastructure.adapters.vpn.remnawave import RemnawavePanel
    async def handler(request):
        assert request.method == 'GET'
        return httpx.Response(200, json=panel_response(description='unrelated account'))
    async with httpx.AsyncClient(base_url='https://panel.invalid', transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PanelUnavailableError):
            await RemnawavePanel(client).create(VpnSubscription(user_guid=GUID), EXPIRES)


async def test_renew_patch_is_absolute_and_selects_numeric_id():
    from app.infrastructure.adapters.vpn.remnawave import RemnawavePanel
    async def handler(request):
        assert request.method == 'PATCH'
        assert json.loads(request.content) == {'id': 17, 'expireAt': '2026-11-01T00:00:00Z', 'status': 'ACTIVE'}
        return httpx.Response(200, json=panel_response())
    async with httpx.AsyncClient(base_url='https://panel.invalid', transport=httpx.MockTransport(handler)) as client:
        result = await RemnawavePanel(client).update(VpnSubscription(
            user_guid=GUID, external_id=17, desired_expires_at=EXPIRES))
    assert result.expires_at == EXPIRES


async def test_expiry_disable_omits_past_expiry_rejected_by_panel(monkeypatch):
    from app.infrastructure.adapters.vpn import remnawave
    monkeypatch.setattr(remnawave, 'get_current_datetime', lambda: EXPIRES + timedelta(seconds=1))
    async def handler(request):
        assert request.method == 'PATCH'
        body = json.loads(request.content)
        if 'expireAt' in body:
            return httpx.Response(400, json={'errors': [{'path': ['expireAt'],
                'message': 'Expiration date cannot be in the past', 'code': 'custom'}]})
        assert body == {'id': 17, 'status': 'DISABLED'}
        return httpx.Response(200, json=panel_response(status='DISABLED'))
    async with httpx.AsyncClient(base_url='https://panel.invalid', transport=httpx.MockTransport(handler)) as client:
        result = await remnawave.RemnawavePanel(client).update(VpnSubscription(
            user_guid=GUID, external_id=17, desired_expires_at=EXPIRES, desired_status='DISABLED'))
    assert result.external_id == 17
    assert result.expires_at == EXPIRES and result.status == 'DISABLED'


async def test_disable_with_future_target_preserves_absolute_expiry(monkeypatch):
    from app.infrastructure.adapters.vpn import remnawave
    monkeypatch.setattr(remnawave, 'get_current_datetime', lambda: EXPIRES - timedelta(days=1))
    async def handler(request):
        assert json.loads(request.content) == {'id': 17, 'status': 'DISABLED', 'expireAt': '2026-11-01T00:00:00Z'}
        return httpx.Response(200, json=panel_response(status='DISABLED'))
    async with httpx.AsyncClient(base_url='https://panel.invalid', transport=httpx.MockTransport(handler)) as client:
        result = await remnawave.RemnawavePanel(client).update(VpnSubscription(
            user_guid=GUID, external_id=17, desired_expires_at=EXPIRES, desired_status='DISABLED'))
    assert result.expires_at == EXPIRES and result.status == 'DISABLED'


@pytest.mark.parametrize('status', [200, 400, 401, 403, 409, 429, 500])
async def test_create_requires_201_and_sanitizes_failures(status):
    from app.infrastructure.adapters.vpn.remnawave import RemnawavePanel
    async def handler(request):
        if request.method == 'GET':
            return httpx.Response(404)
        return httpx.Response(status, json={'secret': 'must-never-escape'})
    async with httpx.AsyncClient(base_url='https://panel.invalid', transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PanelUnavailableError) as error:
            await RemnawavePanel(client).create(VpnSubscription(user_guid=GUID), EXPIRES)
    assert 'must-never-escape' not in str(error.value)


@pytest.mark.parametrize('response', [panel_response(expireAt='2026-11-01T00:00:00'),
                                      panel_response(id='17'), {'response': []}, {'bad': 'secret'}])
async def test_rejects_malformed_remote_data(response):
    from app.infrastructure.adapters.vpn.remnawave import RemnawavePanel
    async def handler(request):
        return httpx.Response(200, json=response)
    async with httpx.AsyncClient(base_url='https://panel.invalid', transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PanelUnavailableError):
            await RemnawavePanel(client).create(VpnSubscription(user_guid=GUID), EXPIRES)


@pytest.mark.parametrize('overrides', [
    {'expireAt': '2026-10-31T00:00:00Z'},
    {'status': 'DISABLED'},
])
async def test_patch_rejects_unconfirmed_expiry_or_status(overrides):
    from app.infrastructure.adapters.vpn.remnawave import RemnawavePanel
    async def handler(request):
        return httpx.Response(200, json=panel_response(**overrides))
    async with httpx.AsyncClient(base_url='https://panel.invalid', transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PanelUnavailableError):
            await RemnawavePanel(client).update(VpnSubscription(
                user_guid=GUID, external_id=17, desired_expires_at=EXPIRES))


async def test_patch_accepts_panel_millisecond_precision():
    from app.infrastructure.adapters.vpn.remnawave import RemnawavePanel
    async def handler(request):
        return httpx.Response(200, json=panel_response(expireAt='2026-11-01T00:00:00.123Z'))
    async with httpx.AsyncClient(base_url='https://panel.invalid', transport=httpx.MockTransport(handler)) as client:
        result = await RemnawavePanel(client).update(VpnSubscription(
            user_guid=GUID, external_id=17, desired_expires_at=EXPIRES.replace(microsecond=123456)))
    assert result.expires_at == EXPIRES.replace(microsecond=123000)


async def test_past_target_accepts_expired_remote_state_for_final_expiry_processing():
    from app.infrastructure.adapters.vpn.remnawave import RemnawavePanel
    past_target = datetime(2020, 1, 1, tzinfo=UTC)
    async def handler(request):
        return httpx.Response(200, json=panel_response(expireAt='2020-01-01T00:00:00Z', status='EXPIRED'))
    async with httpx.AsyncClient(base_url='https://panel.invalid', transport=httpx.MockTransport(handler)) as client:
        result = await RemnawavePanel(client).update(VpnSubscription(
            user_guid=GUID, external_id=17, desired_expires_at=past_target))
    assert result.expires_at == past_target


@pytest.mark.parametrize('overrides,methods', [
    ({}, ['GET']),
    ({'expireAt': '2026-10-31T00:00:00Z'}, ['GET', 'PATCH']),
    ({'status': 'DISABLED'}, ['GET', 'PATCH']),
])
async def test_reconciliation_checks_owned_state_and_patches_only_drift(overrides, methods):
    from app.infrastructure.adapters.vpn.remnawave import RemnawavePanel
    observed = []
    async def handler(request):
        observed.append(request.method)
        return httpx.Response(200, json=panel_response(**overrides) if request.method == 'GET' else panel_response())
    async with httpx.AsyncClient(base_url='https://panel.invalid', transport=httpx.MockTransport(handler)) as client:
        result = await RemnawavePanel(client).reconcile(VpnSubscription(
            user_guid=GUID, external_id=17, desired_expires_at=EXPIRES))
    assert observed == methods
    assert result.expires_at == EXPIRES and result.status == 'ACTIVE'


async def test_reconciliation_rejects_unowned_account_before_patch():
    from app.infrastructure.adapters.vpn.remnawave import RemnawavePanel
    async def handler(request):
        assert request.method == 'GET'
        return httpx.Response(200, json=panel_response(description='unrelated owner'))
    async with httpx.AsyncClient(base_url='https://panel.invalid', transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PanelUnavailableError):
            await RemnawavePanel(client).reconcile(VpnSubscription(
                user_guid=GUID, external_id=17, desired_expires_at=EXPIRES))
