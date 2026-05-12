"""Python auto-startup hook to register user pairlists with freqtrade.

Placed in site-packages so Python loads it on every interpreter start
before freqtrade's own code runs. We need this because freqtrade
validates the pairlist `method` field against a hard-coded enum in
CONF_SCHEMA *before* its resolver scans user_data/pairlists/. Custom
pairlists therefore fail JSON-schema validation unless we add their
names to the enum here.

Adding a new user pairlist? Append its class name to USER_PAIRLISTS.
"""
USER_PAIRLISTS = (
    "CatalystPairList",
)

try:
    from freqtrade.configuration.config_validation import CONF_SCHEMA  # type: ignore
    _enum = CONF_SCHEMA["properties"]["pairlists"]["items"]["properties"]["method"]["enum"]
    for _name in USER_PAIRLISTS:
        if _name not in _enum:
            _enum.append(_name)
except Exception:
    # Anything goes wrong here, don't block Python startup — freqtrade
    # will fail its own validation with a clear error message instead.
    pass

# Tell PairListResolver to also scan user_data/pairlists/. Unlike
# StrategyResolver, the built-in PairListResolver leaves user_subdir
# = None, so user pairlists are silently ignored. Pointing it at
# "pairlists" makes IResolver discover our files in user_data/pairlists/.
try:
    from freqtrade.resolvers.pairlist_resolver import PairListResolver  # type: ignore
    if PairListResolver.user_subdir is None:
        PairListResolver.user_subdir = "pairlists"
except Exception:
    pass
