"""Public facts about ShrimpCoin. Harvest anchoring does not use the token.

The mint was created with the Solana CLI. Mint authority and freeze authority
are revoked. The update authority can still edit token metadata; it is not
required to sign a harvest memo. A later phase could redeem SHRMPCOIN for
prawns. This module only publishes the identifiers so that phase has a place
to start. No transfer, burn, or redemption is implemented.
"""

from __future__ import annotations

from typing import Any

SYMBOL = "SHRMPCOIN"
MINT = "mntsByXQ5QyqMkknKzX1w4CEmErG3RjQbB4zuC6cwKP"
TOKEN_PROGRAM = "token-2022"
UPDATE_AUTHORITY = "bosVqs1t59kXpyuwLFYZ1qf61qWygXEaZYSLGjf1TF4"
SUPPLY = 100_000
MINT_AUTHORITY = None
FREEZE_AUTHORITY = None


def describe() -> dict[str, Any]:
    return {
        "symbol": SYMBOL,
        "mint": MINT,
        "token_program": TOKEN_PROGRAM,
        "update_authority": UPDATE_AUTHORITY,
        "supply": SUPPLY,
        "mint_authority": MINT_AUTHORITY,
        "freeze_authority": FREEZE_AUTHORITY,
        "required_for_harvest_anchor": False,
        "redeem_for_prawns": {
            "phase": "later",
            "implemented": False,
            "note": (
                "A later phase could redeem SHRMPCOIN for prawns. "
                "This hub does not transfer, burn, or escrow tokens."
            ),
        },
    }
