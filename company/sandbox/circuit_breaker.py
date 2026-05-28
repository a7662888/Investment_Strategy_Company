# -*- coding: utf-8 -*-
"""
能力② — 組合層停損 / 熔斷(Circuit Breaker)。

個股停損由策略自己管;這裡是『整個投資組合』的最後防線:
  * 當組合自權益高點回撤超過 halt_drawdown → 觸發熔斷,強制全數轉現金、暫停買進。
  * 熔斷後冷卻 cooldown_days 個交易日才解除,並把高點重設為當前權益
    (給策略乾淨的重新進場機會,且需再跌一次才會重新觸發)。

為何用『時間冷卻』而非『等權益回升』解除:
  熔斷會把部位清成現金,現金不會自己長大;若要求權益回升到高點才解除,
  一旦在崩盤觸發就永遠卡在現金、再也回不來(實測 872 天空手)。
  時間冷卻讓策略在風暴過後能重新評估進場,才符合真實風控。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CircuitBreaker:
    halt_drawdown: float = 0.20    # 回撤超過 20% 觸發熔斷
    cooldown_days: int = 20        # 熔斷後冷卻 N 個交易日才解除
    enabled: bool = True

    peak: float = field(default=0.0, init=False)
    halted: bool = field(default=False, init=False)
    trips: int = field(default=0, init=False)
    halted_days: int = field(default=0, init=False)
    _cooldown: int = field(default=0, init=False)

    def update(self, equity: float) -> bool:
        """餵入當日權益,回傳『是否處於熔斷(應持現金)』。"""
        if not self.enabled:
            return False
        self.peak = max(self.peak, equity)
        if self.peak <= 0:
            return self.halted
        dd = equity / self.peak - 1.0
        if not self.halted:
            if dd <= -self.halt_drawdown:
                self.halted = True
                self.trips += 1
                self._cooldown = self.cooldown_days
        else:
            self._cooldown -= 1
            if self._cooldown <= 0:
                self.halted = False
                self.peak = equity  # 重設高點,需再跌一次才重新熔斷
        if self.halted:
            self.halted_days += 1
        return self.halted
