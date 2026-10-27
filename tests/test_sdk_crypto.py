"""Cross-language wire checks using synthetic keys only; no cloud calls."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridge'))
import eufy_crypto as crypto


def test_sdk_crypto_matches_node_primitives(monkeypatch):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed for the independent cross-language vector')
    # Uses the algorithms and byte encodings from the licensed SDK, with entirely
    # synthetic keys, IV, account-independent text and clock values.
    js = r'''
    const c = require('node:crypto');
    const key = '00112233445566778899aabbccddeeff';
    const iv = Buffer.from(Array.from({length:16}, (_,i)=>i));
    const plain = 'synthetic body';
    const cipher = c.createCipheriv('aes-128-cbc',Buffer.from(key,'hex'),iv);
    const body = Buffer.concat([iv,cipher.update(plain,'utf8'),cipher.final()]).toString('base64');
    const signature = c.createHmac('sha256',Buffer.from(key,'utf8')).update('1700000000+synthetic-once+'+body).digest('hex');
    const a = c.createECDH('prime256v1'); a.setPrivateKey(Buffer.from('01'.padStart(64,'0'),'hex'));
    const b = c.createECDH('prime256v1'); b.setPrivateKey(Buffer.from('02'.padStart(64,'0'),'hex'));
    const shared = a.computeSecret(b.getPublicKey());
    const pc = c.createCipheriv('aes-256-cbc',shared,shared.subarray(0,16));
    const password = Buffer.concat([pc.update('synthetic-password','utf8'),pc.final()]).toString('base64');
    console.log(JSON.stringify({body,signature,shared:shared.toString('hex').slice(0,32),
      password,public:b.getPublicKey('hex','uncompressed')}));
    '''
    vector = json.loads(subprocess.check_output([node, '-e', js], text=True))
    monkeypatch.setattr(crypto.os, 'urandom', lambda count: bytes(range(count)))
    key = '00112233445566778899aabbccddeeff'
    assert crypto.aes_encrypt('synthetic body', key) == vector['body']
    assert crypto.aes_decrypt(vector['body'], key) == 'synthetic body'
    assert crypto.sign(key,1700000000,'synthetic-once',vector['body']) == vector['signature']
    assert crypto.derive_share_key('01',vector['public']) == vector['shared']
    assert crypto.encrypt_password('synthetic-password',vector['public'],ec.derive_private_key(1,ec.SECP256R1())) == vector['password']


def test_shared_secret_keeps_leading_zero_bytes():
    private = ec.derive_private_key(1, ec.SECP256R1())
    # A deterministic public test key whose shared X coordinate begins with 00.
    for value in range(2, 4096):
        other = ec.derive_private_key(value, ec.SECP256R1()).public_key()
        shared = private.exchange(ec.ECDH(), other)
        if shared[0] == 0:
            from cryptography.hazmat.primitives import serialization
            public = other.public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint).hex()
            assert crypto.derive_share_key('01', public) == shared[:16].hex()
            return
    pytest.fail('Synthetic leading-zero fixture was not found')


@pytest.mark.parametrize('value', ['','not base64','YWJjZA=='])
def test_invalid_encrypted_bodies_are_rejected(value):
    with pytest.raises(ValueError):
        crypto.aes_decrypt(value,'00' * 16)
