from types import SimpleNamespace as NS
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridge'))
from media_route import selected_route


def peer(local_type='host', remote_type='host', local='172.30.1.2', remote='192.168.50.14'):
    pair = NS(local_candidate=NS(type=local_type, host=local),
              remote_candidate=NS(type=remote_type, host=remote))
    return NS(sctp=NS(transport=NS(transport=NS(_connection=NS(_nominated={1: pair})))))


def test_nominated_private_hosts_are_direct_private():
    assert selected_route(peer()) == 'direct_private'


@pytest.mark.parametrize('side', ['local', 'remote'])
def test_relay_is_never_claimed_as_lan(side):
    assert selected_route(peer(**{f'{side}_type': 'relay'})) == 'relay'


@pytest.mark.parametrize('address', ['127.0.0.1', '169.254.2.3', '100.64.0.1', '203.0.113.1', 'bad'])
def test_other_addresses_are_not_mislabelled_private_lan(address):
    assert selected_route(peer(remote=address)) == 'other'


def test_missing_nomination_or_library_shape_stays_unknown():
    candidate = peer()
    candidate.sctp.transport.transport._connection._nominated = {}
    assert selected_route(candidate) == 'unknown'
    assert selected_route(NS()) == 'unknown'
