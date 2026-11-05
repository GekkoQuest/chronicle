# Design notes

Chronicle extracts structured offers from rate tables and labeled statements,
then uses SQLite FTS5 to retrieve narrative evidence. Extraction happens during
ingestion; question handling filters and resolves the extracted facts before
rendering an answer. This keeps numeric decisions deterministic and makes their
sources inspectable. The implementation uses Python 3.11+ and the standard library,
with no API keys or external services.

Each snapshot retains its complete page membership. A dated question uses the
latest snapshot on or before the requested date, while an undated question uses
the newest snapshot. Within that observation, explicit effective dates and expiry
dates determine whether an offer applies. Publication dates and fetch timestamps
are stored separately. A later crawl cannot make an old article current, and a
page removed from a full snapshot remains available only in its historical context.

Offers are compared only after matching their product, term, tier, audience, and
metric. Product and rate pages take precedence over calculators and general
articles; later effective dates resolve ties between equally authoritative rate
sources. Conflicting values with equal authority and effective dates cause an
abstention. Answers include the original URL, snapshot ID, and supporting excerpts.
Narrative answers retain explicit reporting years and branch-specific qualifications.

The conflict report groups competing values by qualified topic and metric, cites
evidence for each value, and identifies the latest supported resolution. Active
source disagreements and historical value changes are labeled separately. The
inspector exposes these records alongside historical answers and snapshot changes.

The main tradeoff is coverage. Explicit extraction and intent rules are fast and
testable, but unfamiliar document formats and product aliases can lead to missing
facts. Narrative conflict detection is limited to a few defined claim types.
These limits, along with measured results and remaining evaluation misses, are
documented in [Architecture](docs/ARCHITECTURE.md) and [Evaluation](docs/EVALUATION.md).
