# dr_evt_market

A federation market over DR_EVT. Four platforms that mirror real machines each run one
DR_EVT simulation over the share of the machine they expose. A job stream with bids
enters a queue; at each window the auction takes a prefix of the queue and sends every
winner to the platform it won.

The package requires Python 3.10 or newer. `tests/run_market_tests.sh` selects
a compatible `python3` or `python` executable automatically; set
`PYTHON_EXECUTABLE` to choose one explicitly. The runner uses an importable
`dr_evt` binding from `PYTHONPATH`, or finds a binding built for the selected
interpreter under the configured install prefix (`lib/python` or
`lib64/python`) or the repository's `build` directory.
