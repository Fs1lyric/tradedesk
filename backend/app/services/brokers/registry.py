"""Build the adapter for a broker account by its `broker` column."""

from __future__ import annotations

from collections.abc import Callable

from app.core.config import Settings
from app.core.security import decrypt_secret
from app.models import BrokerAccount
from app.services.brokers.alpaca import AlpacaAdapter
from app.services.brokers.base import BrokerAdapter

BrokerFactory = Callable[[BrokerAccount, Settings], BrokerAdapter]


def _keys(acct: BrokerAccount, settings: Settings) -> tuple[str, str]:
    key = settings.master_key_bytes
    return decrypt_secret(acct.api_key_enc, key), decrypt_secret(acct.api_secret_enc, key)


def alpaca_broker(acct: BrokerAccount, settings: Settings) -> BrokerAdapter:
    key_id, secret = _keys(acct, settings)
    return AlpacaAdapter(key_id, secret, paper=acct.mode == "paper")


BUILDERS: dict[str, BrokerFactory] = {"alpaca": alpaca_broker}


def build_broker(acct: BrokerAccount, settings: Settings) -> BrokerAdapter:
    try:
        builder = BUILDERS[acct.broker]
    except KeyError:
        raise ValueError(f"unsupported broker {acct.broker!r}") from None
    return builder(acct, settings)
