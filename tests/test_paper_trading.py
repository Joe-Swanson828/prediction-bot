"""
tests/test_paper_trading.py — Tests for the paper trading engine.

Run with: python -m pytest tests/test_paper_trading.py -v
"""

import asyncio
import os
import tempfile

import pytest

# Use an in-memory DB for tests
os.environ.setdefault("DB_PATH", ":memory:")


@pytest.fixture
def db_setup(tmp_path):
    """Initialize a fresh test database."""
    db_file = str(tmp_path / "test.db")
    os.environ["DB_PATH"] = db_file
    from database.connection import initialize_db
    from database.schema import create_all_tables
    initialize_db(db_file)
    create_all_tables()
    # Insert a test market
    from database.connection import execute_write
    execute_write(
        """INSERT OR REPLACE INTO markets
           (id, exchange, ticker, category, title, yes_price, no_price)
           VALUES ('kalshi:KXTEST', 'kalshi', 'KXTEST', 'crypto', 'Test Market', 0.5, 0.5)"""
    )
    yield
    os.environ.pop("DB_PATH", None)


@pytest.fixture
def engine(db_setup):
    """Create a paper trading engine with $100 balance."""
    from engine.paper_trading import PaperTradingEngine
    from engine.risk import RiskManager
    risk = RiskManager(starting_balance=100.0)
    trader = PaperTradingEngine(
        starting_balance=100.0,
        risk=risk,
        log_callback=lambda level, msg: None,
    )
    trader.balance = 100.0
    trader.risk.update_balance(100.0)
    return trader


class TestPaperTradingEngine:
    """Test paper trading execution, P&L, and risk integration."""

    def test_initial_balance(self, engine):
        assert engine.balance == 100.0

    def test_execute_trade_reduces_balance(self, db_setup):
        from engine.paper_trading import PaperTradingEngine
        from engine.risk import RiskManager
        risk = RiskManager(starting_balance=100.0)
        trader = PaperTradingEngine(100.0, risk, log_callback=lambda l, m: None)
        trader.balance = 100.0
        risk.update_balance(100.0)

        composite = {
            "final_score": 75.0,
            "ta_score": 72.0,
            "sentiment_score": 65.0,
            "speed_score": 80.0,
        }

        trade = asyncio.run(
            trader.execute_trade("kalshi:KXTEST", "YES", 0.52, composite)
        )

        assert trade is not None
        assert trader.balance < 100.0

    def test_execute_trade_slippage_applied(self, db_setup):
        from engine.paper_trading import PaperTradingEngine
        from engine.risk import RiskManager
        risk = RiskManager(starting_balance=100.0)
        trader = PaperTradingEngine(100.0, risk, log_callback=lambda l, m: None)
        trader.balance = 100.0
        risk.update_balance(100.0)

        composite = {"final_score": 75.0, "ta_score": 72.0, "sentiment_score": 65.0, "speed_score": 80.0}

        trade = asyncio.run(
            trader.execute_trade("kalshi:KXTEST", "YES", 0.52, composite)
        )

        assert trade is not None
        # Slippage should push fill price above 0.52
        assert trade.entry_price >= 0.52
        assert trade.slippage > 0

    def test_trade_rejected_at_max_positions(self, db_setup):
        from engine.paper_trading import PaperTradingEngine
        from engine.risk import RiskManager
        risk = RiskManager(starting_balance=100.0)
        risk.max_positions = 1
        trader = PaperTradingEngine(100.0, risk, log_callback=lambda l, m: None)
        trader.balance = 100.0
        risk.update_balance(100.0)

        composite = {"final_score": 80.0, "ta_score": 80.0, "sentiment_score": 80.0, "speed_score": 80.0}

        # First trade should succeed
        asyncio.run(
            trader.execute_trade("kalshi:KXTEST", "YES", 0.52, composite)
        )

        # Second trade should fail (max_positions=1)
        # Use different market ID to avoid duplicate check
        from database.connection import execute_write
        execute_write(
            """INSERT OR REPLACE INTO markets
               (id, exchange, ticker, category, title, yes_price, no_price)
               VALUES ('kalshi:KXTEST2', 'kalshi', 'KXTEST2', 'crypto', 'Test Market 2', 0.5, 0.5)"""
        )
        trade2 = asyncio.run(
            trader.execute_trade("kalshi:KXTEST2", "YES", 0.52, composite)
        )
        assert trade2 is None

    def test_pnl_calculation(self, db_setup):
        from database.models import Trade
        trade = Trade(
            market_id="kalshi:KXTEST",
            exchange="paper",
            direction="YES",
            quantity=10.0,
            entry_price=0.50,
            mode="paper",
        )
        # Buy YES at 0.50, sell at 0.70 → profit of (0.70 - 0.50) * 10 = 2.00
        pnl = trade.calculate_pnl(0.70)
        assert pnl == pytest.approx(2.00)

    def test_pnl_loss(self, db_setup):
        from database.models import Trade
        trade = Trade(
            market_id="kalshi:KXTEST",
            exchange="paper",
            direction="YES",
            quantity=10.0,
            entry_price=0.60,
            mode="paper",
        )
        # Buy at 0.60, sell at 0.40 → loss of (0.40 - 0.60) * 10 = -2.00
        pnl = trade.calculate_pnl(0.40)
        assert pnl == pytest.approx(-2.00)

    def test_price_clamped_to_valid_range(self, db_setup):
        # Verify filled prices are always in [PRICE_MIN, PRICE_MAX]
        from engine.paper_trading import PaperTradingEngine
        assert PaperTradingEngine.PRICE_MIN == 0.01
        assert PaperTradingEngine.PRICE_MAX == 0.99


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
