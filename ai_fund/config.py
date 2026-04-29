"""Environment-backed config. Fail fast if anything is missing."""
import os
from dotenv import load_dotenv

load_dotenv()


def _required(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").lower()

# Only require the API key for the active provider
ANTHROPIC_API_KEY = _required("ANTHROPIC_API_KEY") if LLM_PROVIDER == "anthropic" else os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY    = _required("OPENAI_API_KEY")    if LLM_PROVIDER == "openai"     else os.environ.get("OPENAI_API_KEY", "")

ALPACA_API_KEY    = _required("ALPACA_API_KEY")
ALPACA_SECRET_KEY = _required("ALPACA_SECRET_KEY")
ALPACA_PAPER      = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
DATABASE_URL      = _required("DATABASE_URL")

# Model assignments — tiered by task complexity.
# Swap individual lines as you tune cost vs quality.

# Anthropic models (active when LLM_PROVIDER=anthropic)
# MODEL_SCOUT            = "claude-haiku-4-5-20251001"  # high-volume screening
# MODEL_TECHNICAL        = "claude-sonnet-4-6"           # structured indicator reasoning
# MODEL_FUNDAMENTAL      = "claude-opus-4-7"             # deep memo writing
# MODEL_PORTFOLIO_MANAGER= "claude-opus-4-7"             # final judgment + execution

# OpenAI models (active when LLM_PROVIDER=openai)
MODEL_SCOUT             = "gpt-4o-mini"  # high-volume screening
MODEL_TECHNICAL         = "gpt-4o"       # structured indicator reasoning
MODEL_FUNDAMENTAL       = "gpt-4o"       # deep memo writing
MODEL_PORTFOLIO_MANAGER = "gpt-4o"       # final judgment + execution
