# -*- coding: utf-8 -*-
"""Shared policy classifications for the single value engine."""

# These businesses can look cheapest on PER near peak earnings. They must be
# assessed on balance-sheet valuation and the position of earnings in the cycle.
CYCLICAL = {
    "1101",  # cement
    "1301",  # petrochemicals
    "2002",  # steel
    "2027",  # steel / aluminium distribution and processing
    "2603",  # shipping
    "2344",  # commodity memory
    "2408",  # commodity memory
    "3006",  # commodity memory
}


# Financial holdings and banks have no gross or operating margin, so the generic
# industrial gates cannot be applied. They use ROE plus PBR instead.
FINHOLD = {
    "2880",  # Hua Nan Financial
    "2881",  # Fubon Financial
    "2882",  # Cathay Financial
    "2883",  # KGI Financial
    "2884",  # E.SUN Financial
    "2885",  # Yuanta Financial
    "2886",  # Mega Financial
    "2887",  # Taishin Financial
    "2890",  # SinoPac Financial
    "2891",  # CTBC Financial
    "2892",  # First Financial
    "5880",  # Taiwan Cooperative Financial
}
