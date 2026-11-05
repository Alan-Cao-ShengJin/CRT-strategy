"""Acquire the predefined extra instruments with the existing API entitlement."""
import json
from research import download_breadth
from research.pool_markets import DEST, PROTOCOL, register_pool


def main():
    register_pool()
    # Only this isolated downloader process changes the acquisition destination.
    # Original breadth-study datasets/calendars and their hashes stay immutable.
    download_breadth.DEST = DEST
    acquisition = download_breadth.Acquisition()
    unavailable = {}
    for symbol in json.loads(PROTOCOL.read_text())['added_symbols']:
        try:
            acquisition.run(symbol)
        except Exception as exc:
            unavailable[symbol] = type(exc).__name__+': '+str(exc)
            print(json.dumps(dict(stage='unavailable', symbol=symbol, error=unavailable[symbol])), flush=True)
        (DEST/'acquisition_status.json').write_text(json.dumps(dict(complete=False, unavailable=unavailable)))
    (DEST/'acquisition_status.json').write_text(json.dumps(dict(complete=True, unavailable=unavailable)))


if __name__ == '__main__':
    main()
