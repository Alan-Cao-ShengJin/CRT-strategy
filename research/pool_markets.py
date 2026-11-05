"""Explicit opt-in registration and immutable dataset lookup for pool research."""
import json
from pathlib import Path

from research.expanded_markets import EXTRA, Market, register

PROTOCOL = Path('research/pool_protocol.json')
DEST = Path('data/choch_pool')


def register_pool():
    for s in json.loads(PROTOCOL.read_text())['added_symbols']:
        EXTRA[s] = Market(s, 'stock', .01, 1., 'US_STOCKS', 'stock', s)
    register()


def ready_path(symbol):
    rules = json.loads(PROTOCOL.read_text())
    directory = DEST if symbol in rules['added_symbols'] else Path('data/choch_expanded')
    return directory/f'READY_{symbol}.json'


def universe():
    old = json.loads(Path('research/choch_breadth_protocol.json').read_text())['symbols']
    return old+json.loads(PROTOCOL.read_text())['added_symbols']
