"""Money is integer centavos end to end; text only at the edges."""


def format_centavos(centavos: int) -> str:
    """12345 -> '123.45'. Negative amounts never occur in output."""
    if not isinstance(centavos, int) or isinstance(centavos, bool):
        raise TypeError(f"money must be int centavos, got {centavos!r}")
    sign = "-" if centavos < 0 else ""
    c = abs(centavos)
    return f"{sign}{c // 100}.{c % 100:02d}"


def format_pesos(centavos: int) -> str:
    """Human display with thousands separators: 400000050 -> '₱4,000,000.50'."""
    c = abs(centavos)
    return f"{'-' if centavos < 0 else ''}₱{c // 100:,}.{c % 100:02d}"


def pesos_to_centavos(pesos: int) -> int:
    return pesos * 100


ROUND_THOUSAND = 1_000_00  # ₱1,000 in centavos
ROUND_HUNDRED = 100_00  # ₱100 in centavos


def is_round_thousand(centavos) -> bool:
    """A whole multiple of ₱1,000: the plug-row tell (FR-F6, FR-I5)."""
    return centavos > 0 and centavos % ROUND_THOUSAND == 0


def is_round_amount(centavos) -> bool:
    """A whole multiple of ₱100: what the round-amount share counts (FR-I4 (2), FR-B4).

    The ledger reader must use this same definition for `round_amount_share`.
    """
    return centavos > 0 and centavos % ROUND_HUNDRED == 0


TIDY_CENTS = (0, 50, 75)  # the only cents a rate-card unit price may carry (FR-F5)


def is_whole_peso(centavos) -> bool:
    """No cents: what the whole-peso share counts (FR-F5, FR-I3)."""
    return centavos % 100 == 0


def is_tidy_cents(centavos) -> bool:
    """Whole pesos or .50 / .75, never random cents (FR-F5, T19)."""
    return centavos % 100 in TIDY_CENTS
