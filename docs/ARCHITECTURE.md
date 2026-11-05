# Architecture

## Data model

Each snapshot stores its observation date and complete page membership. Extracted
offers contain a product family, product name, dimensions, measures, and evidence.

Dimensions include term, model-year range, balance tier, loan-amount band,
purchase/refinance purpose, audience, and introductory-rate scope. Measures retain
rate, APR, APY, and minimum balances as separate decimal strings. The semantic key
excludes the value and source so equivalent offers can be compared across versions.

Evidence records contain the source URL, snapshot ID, fetch timestamp, publication
date, section, line number, and excerpt. Table excerpts preserve the header and
supporting row as verbatim fragments; those fragments are not necessarily adjacent
in the original table. Narrative passages retain their original source text while
a normalized copy is indexed for retrieval.

## Temporal selection

An `as_of` query selects the latest snapshot on or before that date. Without a date,
the newest snapshot is selected. Dates before the first observation are unsupported.
Queries after the latest observation use that observation without assuming that the
real website has remained unchanged.

Offers are then filtered by their stated effective dates and expiry dates. Publication
dates are extracted from explicit date lines; an effective-date sentence is not a
publication date. Fetch timestamps identify observations and do not determine a
claim's authority.

Full-snapshot membership controls which pages are current. A page absent from a
later snapshot is retained historically but does not remain in the current evidence
set. Its absence alone does not establish that a product or branch has closed.

## Resolution

Question constraints select applicable products and tiers before source comparison.
A specific vehicle year narrows overlapping model-year ranges. Mortgage amount
bands select the smallest published cap that contains the requested amount, with
purchase and refinance treated separately.

For rate offers, the authority order is product/rate pages, generic pages, news
articles, blog posts, then calculators. Among equal authorities, the later explicit
effective date wins. Equal-rank numerical disagreement causes an abstention.
Agreeing duplicate sources are selected using field completeness, self-references,
and URL length.

Narrative handlers select explicit claims matching the requested subject. Publication
dates resolve equally authoritative statements. Branch schedules retain their branch
and date qualifications; relative-year questions preserve the years stated by reports.

A `Decision` record contains interpreted constraints, selected evidence, alternatives,
and policy reasons. The answer's Boolean confidence flag indicates whether the
requested answer is supported. Conflict reports distinguish active disagreements
from historical value changes and resolve against the newest available snapshot.

## Storage and retrieval

SQLite provides atomic ingestion, persistence, and FTS5/BM25 retrieval. Ingestion
hashes page content, rejects conflicting reuse of snapshot IDs, and permits
idempotent replay. A lock protects the shared connection; parsing and writes finish
before the new snapshot becomes visible. Parsed offers are cached per snapshot.

The interface uses a read-only HTTP API bound to `127.0.0.1`. Static assets are
allowlisted, the Host header is checked, and source text is rendered through DOM
text nodes. The server does not log question URLs.

## Tradeoffs and limitations

The structured read path provides reproducible numeric selection and low latency
without model calls. Its coverage depends on explicit parsing and intent rules.

- Input is Markdown from the supplied snapshots. Linked PDFs, live pages, and
  dynamically omitted content are not fetched.
- Unfamiliar product aliases and document layouts can result in missing facts.
  Arbitrary multi-hop reasoning and conversational memory are not supported.
- Authority rankings are heuristics. Broader use would require institution-specific
  policies and review of unresolved ties.
- Narrative scalar conflict detection covers branch counts and routing numbers.
  Other prose contradictions require additional claim extraction.
- Missing qualifiers can produce a list of applicable tiers. Purchase mortgages are
  the default when purchase/refinance is unspecified.
- Change reports compare extracted evidence sets. A change in evidence does not
  necessarily change the selected answer.
- Abstention means the implementation found insufficient support, not that the
  underlying information cannot exist elsewhere.
- The HTTP server supports local inspection. It is not an authenticated production
  service.
