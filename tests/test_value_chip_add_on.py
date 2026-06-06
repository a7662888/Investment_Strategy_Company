from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from company.operator.base import PositionState
from company.operator.value_chip import ValueChipOperator


class FakeStockView:
    def __init__(self, close=100.0, per=15.0, yoy=0.1, inst=100):
        self._close = close
        self._per = per
        self._yoy = yoy
        self._inst = inst

    def close(self):
        return self._close

    def per(self):
        return self._per

    def rev_yoy(self):
        return self._yoy

    def inst_net(self, _lookback):
        return self._inst


def test_initial_value_entry_starts_with_half_position():
    op = ValueChipOperator()
    decision = op.decide(FakeStockView(), PositionState())

    assert decision.target_exposure == 0.5
    assert decision.signals["entry_type"] == "initial_half_position"
    assert "半倉" in decision.reason


def test_value_pullback_adds_only_inside_planned_zone():
    op = ValueChipOperator()
    state = PositionState(exposure=0.5, avg_cost=100.0)

    decision = op.decide(FakeStockView(close=93.0), state)

    assert decision.target_exposure == 1.0
    assert decision.signals["add_on_type"] == "value_pullback"
    assert decision.signals["pullback_zone"] is True
    assert "逢低加碼" in decision.reason


def test_deep_pullback_holds_instead_of_adding_before_stop_loss():
    op = ValueChipOperator()
    state = PositionState(exposure=0.5, avg_cost=100.0)

    decision = op.decide(FakeStockView(close=89.0), state)

    assert decision.target_exposure == 0.5
    assert decision.signals["can_value_add_on"] is False
    assert "不加碼" in decision.reason


def test_institutional_selling_blocks_add_on_and_reduces():
    op = ValueChipOperator()
    state = PositionState(exposure=0.5, avg_cost=100.0)

    decision = op.decide(FakeStockView(close=93.0, inst=-100), state)

    assert decision.target_exposure == 0.3
    assert "轉賣超" in decision.reason


def test_stop_loss_overrides_value_add_on():
    op = ValueChipOperator()
    state = PositionState(exposure=0.5, avg_cost=100.0)

    decision = op.decide(FakeStockView(close=84.0), state)

    assert decision.target_exposure == 0.0
    assert "停損" in decision.reason


if __name__ == "__main__":
    test_initial_value_entry_starts_with_half_position()
    test_value_pullback_adds_only_inside_planned_zone()
    test_deep_pullback_holds_instead_of_adding_before_stop_loss()
    test_institutional_selling_blocks_add_on_and_reduces()
    test_stop_loss_overrides_value_add_on()
    print("test_value_chip_add_on: PASS")
