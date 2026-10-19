"""Build the adapter for a broker account by its `broker` column."""

from __future__ import annotations

from collections.abc import Callable

from app.core.config import Settings
from app.core.security import decrypt_secret
from app.models import BrokerAccount
from app.services.brokers.alpaca import AlpacaAdapter
from app.services.brokers.base import BrokerAdapter
from app.services.brokers.ibkr import IbkrAdapter
from app.services.brokers.schwab import SchwabAdapter, parse_credentials

BrokerFactory = Callable[[BrokerAccount, Settings], BrokerAdapter]


def _keys(acct: BrokerAccount, settings: Settings) -> tuple[str, str]:
    key = settings.master_key_bytes
    return decrypt_secret(acct.api_key_enc, key), decrypt_secret(acct.api_secret_enc, key)


def alpaca_broker(acct: BrokerAccount, settings: Settings) -> BrokerAdapter:
    key_id, secret = _keys(acct, settings)
    return AlpacaAdapter(key_id, secret, paper=acct.mode == "paper")


def ibkr_broker(acct: BrokerAccount, settings: Settings) -> BrokerAdapter:
    """api_key is the IBKR account id, api_secret the gateway URL. Paper or live is decided by
    which login the gateway holds."""
    account_id, gateway_url = _keys(acct, settings)
    return IbkrAdapter(account_id, gateway_url)


def schwab_broker(acct: BrokerAccount, settings: Settings) -> BrokerAdapter:
    """api_key is the account hash, api_secret the JSON credentials (see parse_credentials)."""
    account_hash, secret = _keys(acct, settings)
    return SchwabAdapter(account_hash, *parse_credentials(secret))


BUILDERS: dict[str, BrokerFactory] = {
    "alpaca": alpaca_broker, "ibkr": ibkr_broker, "schwab": schwab_broker,
}


def build_broker(acct: BrokerAccount, settings: Settings) -> BrokerAdapter:
    try:
        builder = BUILDERS[acct.broker]
    except KeyError:
        raise ValueError(f"unsupported broker {acct.broker!r}") from None
    return builder(acct, settings)
