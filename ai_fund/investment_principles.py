"""
User-defined investment principles and preferences.

Edit this file to personalize how the Portfolio Manager invests on your behalf.
Each section is a plain-English preference that the PM will weigh alongside
its quantitative rules.  Changes here are picked up on the next pipeline run —
no restart required.
"""

PRINCIPLES = """
# Investor Profile

## Goal
Long-term capital appreciation over a 5–10 year horizon. The fund is paper-trading
but should be managed as if the capital is real and irreplaceable. Avoid decisions
optimized for short-term performance at the expense of long-term compounding.

## Risk Tolerance
Moderate. Concentration in a handful of high-conviction positions is acceptable,
but permanent capital loss is not. Prioritize downside protection over upside
optionality when the two are in conflict.

## Investment Style
- Prefer businesses with durable competitive moats: network effects, switching costs,
  cost advantages, or intangible assets (brands, patents, regulatory licenses).
- Prefer free-cash-flow generation over revenue growth. A business that converts
  earnings to cash is worth more than a high-growth business that burns cash.
- Valuation matters. Pay a fair price for a great business rather than a great price
  for a mediocre one. Avoid chasing momentum or recent winners.

## Preferred Sectors
Allocate toward these sectors when fundamentals and price are both compelling:
- **Technology** — especially software and platform businesses that are well positioned to
benefit from the AI transition (SaaS, cloud infrastructure, payments).
- **Healthcare** — pharmaceuticals and medical devices with strong IP; avoid pure
  biotech with binary drug-trial outcomes unless conviction is very high.
- **Consumer Staples** — resilient cash flows, pricing power, and recession resistance.
- **Industrials** — companies benefiting from long-cycle infrastructure or reshoring trends.

## Sectors to Underweight or Avoid
- **Fossil fuels / traditional energy** — secular headwinds from the energy transition;
  avoid unless a specific company has a credible and near-term transition plan.
- **Gambling, tobacco, alcohol** — ethical preference; avoid.
- **Airlines and cruise lines** — commoditized, capital-intensive, and prone to
  external shocks (fuel, pandemics, labor). Require an exceptional setup to consider.
- **Money-losing growth companies** — avoid companies with negative FCF for more than
  3 consecutive years unless there is a very clear and near-term path to profitability.

## Market Capitalization
- Prefer large-cap (>$10B market cap) for core positions — better data quality,
  analyst coverage, and liquidity.
- Mid-cap ($2B–$10B) positions are acceptable for high-conviction ideas.
- Avoid micro-cap (<$300M) — data quality is poor and liquidity risk is high.

## ESG / Ethical Constraints
Do not invest in companies with material, ongoing controversies involving:
- Child labor or forced labor in the supply chain.
- Systematic environmental violations (not one-off fines, but recurring patterns).
- Governance failures such as repeated accounting restatements or insider fraud.

## Behavioral Guardrails
- Do not chase a stock that has already run up significantly before the analysis
  was completed. If the price has moved more than 15% above the fair value estimate,
  HOLD and wait for a better entry.
- Do not panic-sell on a single bad quarter. Revisit the thesis, but only SELL if
  the long-term competitive position has materially deteriorated.
- Avoid doubling down on losing positions without a fresh fundamental analysis
  confirming the thesis is intact.
"""
