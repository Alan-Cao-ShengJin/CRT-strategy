import json

import pytest

from crt.config import Config
from crt.models import Bar,timestamp
from research.frequency_study import FrequencyAsset,configure


@pytest.mark.parametrize('kind',['stock','futures'])
@pytest.mark.parametrize('variant',['baseline','wider_hours','repeat_setups','wider_and_repeat'])
def test_frequency_changes_do_not_change_risk_or_execution_rules(kind,variant):
    policy=json.load(open('research/frequency_protocol.json'))
    base=Config(symbols=['MNQ'],risk_fraction=.01)
    before=base.to_dict()
    new=configure(base,kind,policy['variants'][variant],policy)
    allowed={'sessions','max_trades_per_day'}
    assert all(new.to_dict()[k]==v for k,v in before.items() if k not in allowed)
    assert base.to_dict()==before
    assert new.max_consecutive_losses==2
    assert new.max_trades_per_day==(6 if policy['variants'][variant]['repeat_setups'] else 2)
    if policy['variants'][variant]['wider_hours']:
        assert new.sessions['new_york']==['09:30','15:30']


@pytest.mark.parametrize('repeat',[False,True])
def test_repeat_releases_session_reservation_without_recycling_pivots(repeat):
    costs=json.load(open('research/choch_breadth_protocol.json'))
    cfg=Config(symbols=['MNQ'])
    e=FrequencyAsset(None,cfg,5,costs,repeat)
    e._contract('MNQ','MNQZ6')
    e.states['MNQ'].selected.add('2026-11-03:new_york')
    marker=(1,timestamp('2026-11-03T14:00:00Z'))
    e.structures['MNQ'].consumed.add(marker)
    e._learn(Bar('MNQ',timestamp('2026-11-03T14:30:00Z'),20000,20001,19999,20000,1,'MNQZ6'))
    assert bool(e.states['MNQ'].selected)==(not repeat)
    assert marker in e.structures['MNQ'].consumed
