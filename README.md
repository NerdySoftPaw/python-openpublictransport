# python-openpublictransport

Async Python providers for public transport departure boards and stop searches.
Supported protocol families include EFA, OTP2, TRIAS, FPTF, HAFAS and GTFS-RT.

## Installation

```sh
pip install python-openpublictransport
```

Python 3.11 or newer is required. Provider access requirements vary; API keys are
supplied by the caller and are not included in this package.

## Connect OpenService

The `connect_trias` provider uses Connect Fahrplanauskunft GmbH's TRIAS 1.2 API.
It supports stop searches, departures and stop-to-stop trip planning through
`fetch_trips(origin_id, destination_id, departure_time=None, limit=3)`.
Explicit departure times must include a timezone; the result limit is 1 to 10.

Each user needs registered access and must observe the
[provider's usage conditions](https://connect-fahrplanauskunft.de/wp-content/uploads/2024/01/Nutzungsbedingungen-fuer-die-API-Schnittstelle-zum-Abruf-von-Fahrplandaten-Stand-29-01-2024.pdf).
Access must not be shared without the provider's permission. Public displays
must include **Alle Angaben ohne Gewähr**. Software licensing does not grant
rights to provider credentials or data.

## Development

```sh
pip install -e '.[dev]'
pytest tests
pip install build
python -m build
```

The Home Assistant integration is maintained in
[NerdySoftPaw/openpublictransport](https://github.com/NerdySoftPaw/openpublictransport).
