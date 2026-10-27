"""Exercise the new protocol adapter without Eufy credentials or networking."""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridge'))
import cloud_transport as transport
from eufy_crypto import aes_decrypt, aes_encrypt, derive_share_key, sign


@pytest.mark.asyncio
async def test_exchange_wraps_public_key_and_uses_the_server_shared_secret():
    bootstrap = '00' * 16
    server_private = ec.derive_private_key(2, ec.SECP256R1())
    server_public = server_private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint).hex()
    captured = {}
    async def send(url, **kwargs):
        headers = kwargs['headers']; encrypted = kwargs['json_body']['client_public_key']
        client_public = aes_decrypt(encrypted, bootstrap)
        assert headers['X-Signature'] == sign(bootstrap, headers['X-Request-Ts'],headers['X-Request-Once'], encrypted)
        assert headers['X-Auth-Token'] == 'synthetic-token'
        captured['shared'] = derive_share_key('02',client_public)
        return {'status':200, 'json':{'code':0,'data':{'server_public_key':aes_encrypt(server_public,bootstrap)}}}
    key_id, private, public, shared = await transport.exchange(send,'https://example.com/exchange',bootstrap, token='synthetic-token', country='AU',timeout=7)
    assert len(key_id) == 32 and len(private) == 64
    assert public == server_public and shared == captured['shared']


@pytest.mark.asyncio
async def test_signed_post_preserves_challenges_and_compatibility_envelopes():
    key = '11' * 16
    challenge = {'captcha_id':'synthetic-challenge','item':'synthetic-image'}
    async def send(url, **kwargs):
        headers = kwargs['headers']
        assert json.loads(aes_decrypt(kwargs['data'],key)) == {'purpose':'synthetic'}
        assert headers['X-Signature'] == sign(key,headers['X-Request-Ts'],headers['X-Request-Once'],kwargs['data'])
        assert headers['Content-Type'] == 'text/plain'
        assert kwargs['timeout'] == 7
        return {'status':200,'json':{'code':10019,'msg':'challenge','data':aes_encrypt(json.dumps(challenge),key)}}
    result = await transport.signed_post(send,'https://example.com/login',{'purpose':'synthetic'},
        key_id='synthetic-key',shared_key=key,token='',gtoken='',country='AU',
        extra_headers={'App-Name':'eufy_mega'},return_envelope=True,timeout=7)
    assert result['code'] == 10019 and result['data'] == challenge


@pytest.mark.asyncio
async def test_http_failure_never_echoes_response_content():
    send = AsyncMock(return_value={'status':403,'json':None,'text':'private-token-like-response'})
    with pytest.raises(transport.CloudProtocolError) as error:
        await transport.signed_post(send,'https://example.com/test',{},key_id='key',shared_key='11'*16,
            token='private-token',gtoken='',country='',extra_headers=None,return_envelope=False,timeout=7)
    assert str(error.value) == 'Cloud request returned HTTP 403'


@pytest.mark.asyncio
async def test_failed_exchange_never_echoes_response_content():
    send = AsyncMock(return_value={'status':200,'json':{'code':123,'data':{}},'text':'private-response'})
    with pytest.raises(transport.CloudProtocolError,match='Cloud key exchange was rejected'):
        await transport.exchange(send,'https://example.com/exchange','00'*16,token='',country='',timeout=7)
