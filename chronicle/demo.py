"""Invented public demo data. No supplied website content is redistributed."""
from .models import Page, Snapshot


def demo_snapshots() -> list[Snapshot]:
    result = []
    for sid, date, auto, apy in (
        ("demo-jan", "2026-01-15", "6.20", "3.40"),
        ("demo-mar", "2026-03-15", "5.80", "3.65"),
        ("demo-may", "2026-05-15", "5.40", "3.90"),
    ):
        def page(path: str, title: str, text: str) -> Page:
            return Page("https://riverstone.example/" + path, title, text, date + "T12:00:00Z")

        pages = [
            page("personal/auto-loans", "Auto loans", f"""# Auto Loans

| Model Year | Term | APR |
| --- | --- | --- |
| 2024-27 Model Year | 60 Months | {auto}% |
| 2020-23 Model Year | 60 Months | 7.10% |

Rates are effective as of {date}.
"""),
            page("calculators/auto", "Auto calculator", """# Auto Loan Calculator

| Model Year | Term | APR |
| --- | --- | --- |
| 2024-27 Model Year | 60 Months | 6.20% |

Rates are effective as of 2026-01-15.
"""),
            page("personal/certificates", "Certificate rates", f"""# Certificate Rates

| Certificate | Dividend Rate | APY | Min. to Earn |
| --- | --- | --- | --- |
| 12 Month Fixed Certificate Special | 3.20% | {apy}%1 | $500 |
| 12 Month Fixed Jumbo Certificate Special | 4.00% | 4.10% | $75,000 |

Rates are effective as of {date}.
"""),
            page("personal/savings", "Share Savings", """# Savings Account Rates

| Account Type | Balance Tier | Dividend Rate | APY |
| --- | --- | --- | --- |
| Share Savings | $0 - $9,999.99 | 0.10% | 0.10% |
| Share Savings | $10,000+ | 0.25% | 0.25% |

Rates are effective as of 2026-01-01.
"""),
            page("contact", "Riverstone branch locations", """# Riverstone Branches

### Riverstone Credit Union - Elm Branch

410 Elm Street, Brookfield, WI 53005

**Lobby**

Monday-Friday: 9 a.m.-5 p.m.

Saturday: 9 a.m.-noon

Call 800-555-0142 for current hours.

Routing Number: 123456780
"""),
            page("personal/student-loans", "Student Loans", """# Student Loans

Choose a new student loan or refinance an existing one. Flexible repayment terms and no origination fees are available.
"""),
        ]
        result.append(Snapshot(sid, date, pages))
    return result
