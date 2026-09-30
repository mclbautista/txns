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
