"""The sticky intraday contract universe.

Once a contract enters the discovery window on a given trading day it is
collected for the remainder of that session, even after spot moves and the
contract leaves the current ATM window.  This is what makes the dataset usable
for realistic entry/exit modelling: a position opened at 09:40 must still have
quotes at 15:55.

Contracts are never removed intraday.  The universe is persisted after every
change so a restart at noon resumes with the full morning universe.

Corrupt metadata is never silently overwritten: the damaged file is preserved
with a ``.corrupt-<timestamp>`` suffix and the failure is logged at ERROR
level before a fresh universe is started (or, with ``strict=True``, the load
raises and the caller decides).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class CorruptMetadataError(RuntimeError):
    """Raised when a contract metadata file cannot be parsed in strict mode."""


@dataclass(frozen=True)
class TrackedContract:
    symbol: str
    ticker: str
    expiration: str
    strike: float | None
    option_type: str | None
    first_discovered_utc: str

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> TrackedContract | None:
        symbol = raw.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            return None
        strike_raw = raw.get("strike")
        try:
            strike = float(strike_raw) if strike_raw is not None else None
        except (TypeError, ValueError):
            strike = None
        option_type = raw.get("option_type")
        return TrackedContract(
            symbol=symbol.strip(),
            ticker=str(raw.get("ticker", "")),
            expiration=str(raw.get("expiration", "")),
            strike=strike,
            option_type=str(option_type).lower() if isinstance(option_type, str) else None,
            first_discovered_utc=str(raw.get("first_discovered_utc", "")),
        )


class ContractTracker:
    """Per ticker, per trading-day sticky contract universe."""

    def __init__(
        self,
        path: Path | str,
        *,
        ticker: str,
        trading_date: date,
        strict: bool = False,
    ) -> None:
        self.path = Path(path)
        self.ticker = ticker
        self.trading_date = trading_date
        self.strict = strict
        self._contracts: dict[str, TrackedContract] = {}
        self._loaded = False

    # ------------------------------------------------------------------ io

    def load(self) -> None:
        """Load an existing same-day universe, if any.  Idempotent."""
        self._loaded = True
        if not self.path.exists():
            logger.info(
                "No existing contract metadata for %s %s; starting a new universe",
                self.ticker,
                self.trading_date.isoformat(),
            )
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            self._handle_corrupt(f"unreadable metadata file: {exc}")
            return

        if not isinstance(payload, dict):
            self._handle_corrupt("metadata root is not a JSON object")
            return

        stored_date = payload.get("trading_date")
        if stored_date and stored_date != self.trading_date.isoformat():
            # A per-day path should make this impossible; treat it as corruption
            # rather than mixing two sessions into one universe.
            self._handle_corrupt(
                f"metadata is for trading date {stored_date}, expected "
                f"{self.trading_date.isoformat()}"
            )
            return

        raw_contracts = payload.get("contracts")
        if not isinstance(raw_contracts, list):
            self._handle_corrupt("contracts field is missing or not a list")
            return

        loaded: dict[str, TrackedContract] = {}
        skipped = 0
        for item in raw_contracts:
            contract = TrackedContract.from_dict(item) if isinstance(item, dict) else None
            if contract is None:
                skipped += 1
                continue
            loaded[contract.symbol] = contract
        if skipped:
            logger.error(
                "Skipped %d unusable contract records while loading %s", skipped, self.path
            )
        self._contracts = loaded
        logger.info(
            "Restored %d tracked contracts for %s %s from %s",
            len(loaded),
            self.ticker,
            self.trading_date.isoformat(),
            self.path,
        )

    def _handle_corrupt(self, reason: str) -> None:
        message = f"Corrupt contract metadata at {self.path}: {reason}"
        if self.strict:
            raise CorruptMetadataError(message)
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        quarantine = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        try:
            os.replace(self.path, quarantine)
            logger.error("%s; preserved as %s and starting a fresh universe", message, quarantine)
        except OSError as exc:
            logger.error("%s; could not quarantine the file (%s)", message, exc)
        self._contracts = {}

    def save(self) -> None:
        """Persist the universe atomically (temp file on the same filesystem)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "ticker": self.ticker,
            "trading_date": self.trading_date.isoformat(),
            "updated_utc": datetime.now(tz=UTC).isoformat(),
            "contract_count": len(self._contracts),
            "contracts": [asdict(c) for c in self.sorted_contracts()],
        }
        tmp_path = self.path.with_name(f"{self.path.name}.tmp-{os.getpid()}")
        try:
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp_path, self.path)
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------- universe

    def add_contracts(
        self,
        contracts: list[dict[str, Any]],
        *,
        expiration: str,
        discovered_utc: datetime | None = None,
    ) -> list[TrackedContract]:
        """Add newly discovered contracts; returns only the genuinely new ones.

        Duplicates keep their original ``first_discovered_utc``.
        """
        if not self._loaded:
            self.load()
        stamp = (discovered_utc or datetime.now(tz=UTC)).astimezone(UTC).isoformat()
        new: list[TrackedContract] = []
        for raw in contracts:
            symbol = raw.get("symbol")
            if not isinstance(symbol, str) or not symbol.strip():
                continue
            symbol = symbol.strip()
            if symbol in self._contracts:
                continue
            strike_raw = raw.get("strike")
            try:
                strike = float(strike_raw) if strike_raw is not None else None
            except (TypeError, ValueError):
                strike = None
            option_type = raw.get("option_type")
            contract = TrackedContract(
                symbol=symbol,
                ticker=self.ticker,
                expiration=str(raw.get("expiration_date") or expiration),
                strike=strike,
                option_type=str(option_type).lower() if isinstance(option_type, str) else None,
                first_discovered_utc=stamp,
            )
            self._contracts[symbol] = contract
            new.append(contract)
        if new:
            self.save()
        return new

    def symbols(self) -> list[str]:
        """All tracked OCC symbols, in a stable order."""
        if not self._loaded:
            self.load()
        return [c.symbol for c in self.sorted_contracts()]

    def sorted_contracts(self) -> list[TrackedContract]:
        return sorted(
            self._contracts.values(),
            key=lambda c: (
                c.strike if c.strike is not None else 0.0,
                c.option_type or "",
                c.symbol,
            ),
        )

    def get(self, symbol: str) -> TrackedContract | None:
        return self._contracts.get(symbol)

    def __len__(self) -> int:
        if not self._loaded:
            self.load()
        return len(self._contracts)

    def __contains__(self, symbol: object) -> bool:
        return isinstance(symbol, str) and symbol in self._contracts
